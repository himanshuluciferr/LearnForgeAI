"""Runs one generation job and maps workflow events onto job progress."""

from __future__ import annotations

import logging
from uuid import uuid4

from backend.models.course import StoredCourse
from backend.models.job import JobStatus
from backend.schemas.course import CourseRequest
from backend.services.course_store import course_store
from backend.services.course_index import index_course
from backend.services.job_store import job_store
from backend.workflow.state import (
    Clarification,
    CourseState,
    Rejection,
    SubjectConfirmation,
    WorkflowStep,
)
from backend.workflow.workflow import build_confirmed_workflow, build_workflow

logger = logging.getLogger(__name__)

# Not every executor is a progress step; the rejection, clarify and confirm nodes have no weight.
_STEP_IDS = {step.value for step in WorkflowStep}

# Any of these arriving as workflow output means the run stopped early, on purpose.
TerminalOutput = Rejection | Clarification | SubjectConfirmation
EarlyExit = (Rejection, Clarification, SubjectConfirmation)


async def _drive(workflow, state: CourseState, update) -> TerminalOutput | None:
    """Streams one run, reporting progress and returning whatever early exit it produced."""
    stopped: TerminalOutput | None = None
    async for event in workflow.run(state, stream=True):
        if event.type == "executor_completed" and str(event.executor_id) in _STEP_IDS:
            await update(step=WorkflowStep(event.executor_id), percent=state.percent)
        elif event.type == "output" and isinstance(event.data, EarlyExit):
            stopped = event.data
    return stopped


async def _record_early_exit(
    stopped: TerminalOutput, job_id: str, user_id: str, state: CourseState, update
) -> None:
    if isinstance(stopped, Rejection):
        await update(status=JobStatus.REJECTED, detail=stopped.message)
        return
    if isinstance(stopped, Clarification):
        await update(
            status=JobStatus.NEEDS_CHOICE, detail=stopped.message, options=stopped.options
        )
        return
    # The draft is stored under the job's own id so the second run replays the subject the
    # learner approved, rather than searching again and possibly landing somewhere else.
    await course_store.save(
        StoredCourse(id=job_id, user_id=user_id, job_id=job_id, state=state)
    )
    await update(
        status=JobStatus.NEEDS_CONFIRMATION,
        detail=stopped.message,
        subject_name=stopped.canonical_name,
        subject_description=stopped.description,
        subject_sources=stopped.source_urls,
    )


async def run_generation(
    job_id: str, request: CourseRequest, state: CourseState | None = None
) -> None:
    """Runs one job. `state` is supplied only on the second pass, when the learner has already
    approved the subject and its analysis is replayed rather than recomputed."""

    async def update(**fields) -> None:
        # Every write carries the partition key, so no job update is a cross-partition query.
        await job_store.update(job_id, user_id=request.user_id, **fields)

    await update(status=JobStatus.RUNNING)

    resuming = state is not None
    # Executors mutate this object in place, so it stays the source of truth for progress.
    if state is None:
        state = CourseState(job_id=job_id, user_id=request.user_id, prompt=request.prompt)

    try:
        workflow = build_confirmed_workflow() if resuming else build_workflow()
        stopped = await _drive(workflow, state, update)
        if stopped is not None:
            await _record_early_exit(stopped, job_id, request.user_id, state, update)
            return
        course = await course_store.save(
            StoredCourse(
                id=str(uuid4()), user_id=request.user_id, job_id=job_id, state=state
            )
        )
        # After the save, and deliberately not before: an indexed course that was never stored
        # would be searchable and unreadable. index_course swallows its own failures.
        await index_course(course.id, request.user_id, state)
        await update(status=JobStatus.COMPLETED, percent=state.percent, course_id=course.id)
    except Exception as exc:
        # The failure is recorded on the job, so it must not escape the background task.
        logger.exception("Generation job %s failed", job_id)
        await update(status=JobStatus.FAILED, error=str(exc))
