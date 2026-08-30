"""Generation progress as a live stream.

Server-sent events rather than a poll: generation runs ten steps over several minutes, and a
client asking "done yet?" every two seconds spends nearly all of it being told nothing has
changed.

Honest about what this is not: nothing pushes to us either. The job row is written by a
background task and read here on a timer, so this is a poll moved to the server, where one
loop serves a connection instead of every client running its own. What the client gains is a
single connection, immediate delivery, and no retry logic of its own.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import StreamingResponse

from backend.models.job import GenerationJob, JobStatus
from backend.schemas.course import JobProgress
from backend.services.job_store import job_store

logger = logging.getLogger(__name__)

POLL_SECONDS = 1.0
# Long enough to be quiet, short enough that a proxy idle timeout never sees a silent socket.
HEARTBEAT_SECONDS = 15.0
# A whole generation, with room to spare. The stream ends rather than living forever.
MAX_STREAM_SECONDS = 45 * 60

# Nothing more will happen to a job in one of these, so the stream has said all it can.
SETTLED = {
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.REJECTED,
    JobStatus.NEEDS_CHOICE,
    JobStatus.NEEDS_CONFIRMATION,
}


def frame(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _age(job: GenerationJob) -> int:
    updated = job.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return int((datetime.now(timezone.utc) - updated).total_seconds())


def _tick(job: GenerationJob, last: dict | None) -> tuple[list[str], bool, dict]:
    """Frames to send, whether that is the end, and the state to compare against next time."""
    current = JobProgress.of(job).model_dump(mode="json")
    settled = job.status in SETTLED
    if current != last:
        return [frame("done" if settled else "progress", current)], settled, current
    if settled:
        # Settled before the stream opened, so there was no change to notice.
        return [frame("done", current)], True, current
    return [], False, current


async def events(request: Request, job_id: str, user_id: str) -> AsyncIterator[str]:
    """Emits on change, keeps quiet otherwise, and always ends by saying why."""
    last: dict | None = None
    quiet = 0.0
    elapsed = 0.0

    while True:
        # A browser that navigates away leaves the task running otherwise, one per lost tab.
        if await request.is_disconnected():
            return

        job = await job_store.get(job_id, user_id)
        if job is None:
            yield frame("error", {"detail": "Job not found"})
            return

        frames, finished, current = _tick(job, last)
        quiet = 0.0 if current != last else quiet
        last = current
        for one in frames:
            yield one
        if finished:
            return

        if quiet >= HEARTBEAT_SECONDS:
            quiet = 0.0
            # Seconds since the job last moved, rather than a stall verdict of our own: a
            # background task dies with the process and leaves a job running forever, and the
            # client is better placed to decide how long is too long.
            yield frame("waiting", {"seconds_since_update": _age(job)})

        if elapsed >= MAX_STREAM_SECONDS:
            yield frame("timeout", {"detail": "Stream closed; poll for progress"})
            return

        await asyncio.sleep(POLL_SECONDS)
        quiet += POLL_SECONDS
        elapsed += POLL_SECONDS


def stream_response(request: Request, job_id: str, user_id: str) -> StreamingResponse:
    return StreamingResponse(
        events(request, job_id, user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx buffers a response body by default, which would hold every event back.
            "X-Accel-Buffering": "no",
            # Declaring one keeps the gzip middleware off this response. A compressor waits
            # for enough bytes to be worth emitting, which is exactly the delay a stream
            # exists to avoid.
            "Content-Encoding": "identity",
        },
    )
