"""Runs one generation job and maps workflow events onto job progress."""

from __future__ import annotations

import logging
from uuid import uuid4

from backend.models.course import StoredCourse
from backend.models.job import JobStatus
from backend.schemas.course import CourseRequest
from backend.services.course_store import course_store
from backend.services.job_store import job_store
from backend.workflow.state import Clarification, CourseState, Rejection, WorkflowStep
from backend.workflow.workflow import build_workflow

logger = logging.getLogger(__name__)

# Not every executor is a progress step; the rejection and clarify nodes have no weight.
_STEP_IDS = {step.value for step in WorkflowStep}


async def run_generation(job_id: str, request: CourseRequest) -> None:
    async def update(**fields) -> None:
        # Every write carries the partition key, so no job update is a cross-partition query.
        await job_store.update(job_id, user_id=request.user_id, **fields)

    await update(status=JobStatus.RUNNING, percent=0)

    # Executors mutate this object in place, so it stays the source of truth for progress.
    state = CourseState(job_id=job_id, user_id=request.user_id, prompt=request.prompt)
    rejection: Rejection | None = None
    clarification: Clarification | None = None

    try:
        async for event in build_workflow().run(state, stream=True):
            if event.type == "executor_completed" and str(event.executor_id) in _STEP_IDS:
                await update(step=WorkflowStep(event.executor_id), percent=state.percent)
            elif event.type == "output" and isinstance(event.data, Rejection):
                rejection = event.data
            elif event.type == "output" and isinstance(event.data, Clarification):
                clarification = event.data

        if rejection is not None:
            await update(status=JobStatus.REJECTED, detail=rejection.message)
        elif clarification is not None:
            await update(
                status=JobStatus.NEEDS_CHOICE,
                detail=clarification.message,
                options=clarification.options,
            )
        else:
            course = await course_store.save(
                StoredCourse(
                    id=str(uuid4()),
                    user_id=request.user_id,
                    job_id=job_id,
                    state=state,
                )
            )
            await update(
                status=JobStatus.COMPLETED,
                percent=state.percent,
                course_id=course.id,
            )
    except Exception as exc:
        # The failure is recorded on the job, so it must not escape the background task.
        logger.exception("Generation job %s failed", job_id)
        await update(status=JobStatus.FAILED, error=str(exc))
