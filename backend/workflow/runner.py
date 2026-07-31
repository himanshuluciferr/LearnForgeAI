"""Runs one generation job and maps workflow events onto job progress."""

from __future__ import annotations

import logging

from backend.models.job import JobStatus
from backend.schemas.course import CourseRequest
from backend.services.job_store import job_store
from backend.workflow.state import CourseState, Rejection, WorkflowStep
from backend.workflow.workflow import build_workflow

logger = logging.getLogger(__name__)

# Not every executor is a progress step; the rejection node has no weight.
_STEP_IDS = {step.value for step in WorkflowStep}


async def run_generation(job_id: str, request: CourseRequest) -> None:
    await job_store.update(job_id, status=JobStatus.RUNNING, percent=0)

    # Executors mutate this object in place, so it stays the source of truth for progress.
    state = CourseState(job_id=job_id, user_id=request.user_id, prompt=request.prompt)
    rejection: Rejection | None = None

    try:
        async for event in build_workflow().run(state, stream=True):
            if event.type == "executor_completed" and str(event.executor_id) in _STEP_IDS:
                await job_store.update(
                    job_id,
                    step=WorkflowStep(event.executor_id),
                    percent=state.percent,
                )
            elif event.type == "output" and isinstance(event.data, Rejection):
                rejection = event.data

        if rejection is not None:
            await job_store.update(job_id, status=JobStatus.REJECTED, detail=rejection.message)
        else:
            await job_store.update(job_id, status=JobStatus.COMPLETED, percent=state.percent)
    except Exception as exc:
        # The failure is recorded on the job, so it must not escape the background task.
        logger.exception("Generation job %s failed", job_id)
        await job_store.update(job_id, status=JobStatus.FAILED, error=str(exc))
