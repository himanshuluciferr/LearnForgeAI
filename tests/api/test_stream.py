"""Tests for the progress stream.

The loop is driven by a stub store rather than the clock: a test that waits for real seconds
to pass is slow and, worse, flaky on a busy machine.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.api import stream as stream_api
from backend.main import app
from backend.models.job import GenerationJob, JobStatus
from backend.services.security import create_stream_ticket, create_token
from backend.workflow.state import WorkflowStep

client = TestClient(app)

USER = "priya@contoso.com"
JOB = "job-stream-1"


def job(status: JobStatus, percent: int = 0, step: WorkflowStep | None = None) -> GenerationJob:
    return GenerationJob(
        id=JOB, user_id=USER, prompt="teach me rust", status=status, percent=percent, step=step
    )


class ScriptedStore:
    """Hands out one job per read, so a test can write the sequence the stream should see."""

    def __init__(self, *jobs: GenerationJob | None) -> None:
        self._jobs = list(jobs)
        self.reads: list[str] = []

    async def get(self, job_id: str, user_id: str | None = None) -> GenerationJob | None:
        self.reads.append(user_id or "")
        found = self._jobs.pop(0) if self._jobs else None
        if found is None or (user_id is not None and found.user_id != user_id):
            return None
        return found


@pytest.fixture(autouse=True)
def instant_poll(monkeypatch):
    monkeypatch.setattr(stream_api, "POLL_SECONDS", 0.0)


def ticket(user_id: str = USER) -> dict:
    return {"ticket": create_stream_ticket(user_id)}


def read_stream(params: dict) -> list[tuple[str, dict]]:
    response = client.get(f"/courses/{JOB}/stream", params=params)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = []
    for block in response.text.strip().split("\n\n"):
        if not block.strip():
            continue
        name = block.split("\n")[0].removeprefix("event: ")
        payload = block.split("data: ", 1)[1]
        frames.append((name, json.loads(payload)))
    return frames


# --- getting in ----------------------------------------------------------------------


def test_the_stream_needs_a_ticket():
    assert client.get(f"/courses/{JOB}/stream").status_code == 401


def test_a_session_token_is_not_accepted_in_the_query_string():
    """Otherwise the ticket buys nothing: the long-lived token ends up in the url anyway."""
    session = create_token(USER, USER)

    assert client.get(f"/courses/{JOB}/stream", params={"ticket": session}).status_code == 401


def test_rubbish_is_refused():
    assert client.get(f"/courses/{JOB}/stream", params={"ticket": "nonsense"}).status_code == 401


# --- what it sends -------------------------------------------------------------------


def test_a_finished_job_is_reported_at_once_and_the_stream_ends(monkeypatch):
    """Opening a stream on a job that already finished must not hang waiting for a change."""
    monkeypatch.setattr(
        stream_api, "job_store", ScriptedStore(job(JobStatus.COMPLETED, percent=100))
    )

    frames = read_stream(ticket())

    assert [name for name, _ in frames] == ["done"]
    assert frames[0][1]["status"] == "completed"


def test_each_step_arrives_as_it_happens_and_the_last_one_ends_it(monkeypatch):
    monkeypatch.setattr(
        stream_api,
        "job_store",
        ScriptedStore(
            job(JobStatus.RUNNING, 10, WorkflowStep.RESEARCH),
            job(JobStatus.RUNNING, 30, WorkflowStep.CURRICULUM),
            job(JobStatus.COMPLETED, 100),
        ),
    )

    frames = read_stream(ticket())

    assert [name for name, _ in frames] == ["progress", "progress", "done"]
    assert [payload["percent"] for _, payload in frames] == [10, 30, 100]


def test_an_unchanged_job_is_not_repeated(monkeypatch):
    """The whole point over polling: silence when nothing has happened."""
    monkeypatch.setattr(
        stream_api,
        "job_store",
        ScriptedStore(
            job(JobStatus.RUNNING, 10),
            job(JobStatus.RUNNING, 10),
            job(JobStatus.RUNNING, 10),
            job(JobStatus.COMPLETED, 100),
        ),
    )

    frames = read_stream(ticket())

    assert [name for name, _ in frames] == ["progress", "done"]


def test_a_job_that_stops_to_ask_the_learner_ends_the_stream(monkeypatch):
    """needs-choice is not an error and not progress: nothing more happens until the learner
    answers, so a stream that stayed open would wait forever."""
    monkeypatch.setattr(stream_api, "job_store", ScriptedStore(job(JobStatus.NEEDS_CHOICE)))

    frames = read_stream(ticket())

    assert [name for name, _ in frames] == ["done"]
    assert frames[0][1]["status"] == "needs-choice"


def test_a_failed_job_ends_the_stream(monkeypatch):
    monkeypatch.setattr(stream_api, "job_store", ScriptedStore(job(JobStatus.FAILED)))

    assert [name for name, _ in read_stream(ticket())] == ["done"]


def test_an_unknown_job_says_so_rather_than_hanging(monkeypatch):
    monkeypatch.setattr(stream_api, "job_store", ScriptedStore(None))

    frames = read_stream(ticket())

    assert frames == [("error", {"detail": "Job not found"})]


def test_another_learners_job_is_not_streamed(monkeypatch):
    monkeypatch.setattr(stream_api, "job_store", ScriptedStore(job(JobStatus.RUNNING)))

    frames = read_stream(ticket("mallory"))

    assert frames == [("error", {"detail": "Job not found"})]


# --- staying alive and knowing when to stop -------------------------------------------


def test_silence_is_broken_by_a_heartbeat_carrying_the_age(monkeypatch):
    """A proxy closes an idle socket, and a job stuck in `running` has to be visible as stuck
    without the server inventing a stall verdict of its own."""
    # Real but tiny: a zero heartbeat would fire on the same tick as a change and prove
    # nothing about waiting.
    monkeypatch.setattr(stream_api, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(stream_api, "HEARTBEAT_SECONDS", 0.02)
    monkeypatch.setattr(
        stream_api,
        "job_store",
        ScriptedStore(
            job(JobStatus.RUNNING, 10),
            job(JobStatus.RUNNING, 10),
            job(JobStatus.RUNNING, 10),
            job(JobStatus.RUNNING, 10),
            job(JobStatus.COMPLETED),
        ),
    )

    frames = read_stream(ticket())
    names = [name for name, _ in frames]

    assert names[0] == "progress" and names[-1] == "done"
    assert "waiting" in names
    assert frames[names.index("waiting")][1]["seconds_since_update"] >= 0


def test_the_stream_gives_up_rather_than_living_forever(monkeypatch):
    """A background task dies with the process, leaving a job that says `running` and never
    moves again. Without this the connection is held open for as long as the server lives."""
    monkeypatch.setattr(stream_api, "MAX_STREAM_SECONDS", 0.0)
    monkeypatch.setattr(stream_api, "job_store", ScriptedStore(job(JobStatus.RUNNING, 10)))

    frames = read_stream(ticket())

    assert [name for name, _ in frames] == ["progress", "timeout"]
