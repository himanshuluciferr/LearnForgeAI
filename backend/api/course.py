"""Course generation and retrieval endpoints. Runs the Agent Framework workflow."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from backend.api.deps import CurrentLearner
from backend.models.job import GenerationJob, JobStatus
from backend.schemas.course import (
    ChoiceRequest,
    CourseRequest,
    CourseSummary,
    JobAccepted,
    JobProgress,
    NewCourse,
)
from backend.schemas.document import CourseDocument, as_document
from backend.services.course_store import course_store
from backend.services.job_store import job_store
from backend.workflow.runner import run_generation

router = APIRouter(prefix="/courses", tags=["course"])


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_course(
    body: NewCourse, tasks: BackgroundTasks, learner: CurrentLearner
) -> JobAccepted:
    """Generation takes minutes, so the job is queued and the caller polls for progress."""
    request = CourseRequest(user_id=learner.user_id, prompt=body.prompt, language=body.language)
    job = GenerationJob(id=str(uuid4()), user_id=learner.user_id, prompt=body.prompt)
    await job_store.create(job)
    tasks.add_task(run_generation, job.id, request)
    # The token authorises the poll: the url no longer carries who is asking.
    return JobAccepted(
        job_id=job.id, status=job.status, status_url=f"/courses/{job.id}/progress"
    )


@router.post("/{job_id}/confirm", status_code=status.HTTP_202_ACCEPTED)
async def confirm_subject(
    job_id: str,
    tasks: BackgroundTasks,
    learner: CurrentLearner,
    selection: ChoiceRequest | None = None,
) -> JobAccepted:
    """Answers a clarification or approves the identified subject."""
    job = await job_store.get(job_id, learner.user_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    if job.status is JobStatus.NEEDS_CHOICE:
        if selection is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="A choice is required for a needs-choice job",
            )
        if selection.choice not in job.options:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Choice must be one of the job options",
            )
        prompt = f"{job.prompt}\n\nThe learner selected this subject: {selection.choice}"
        request = CourseRequest(user_id=job.user_id, prompt=prompt)
        tasks.add_task(run_generation, job_id, request)
        return JobAccepted(
            job_id=job.id,
            status=JobStatus.RUNNING,
            status_url=f"/courses/{job.id}/progress",
        )

    if job.status is not JobStatus.NEEDS_CONFIRMATION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is {job.status}, so there is nothing to confirm",
        )
    draft = await course_store.get(job_id, job.user_id)
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="The analysed subject is no longer stored"
        )

    state = draft.state
    state.subject_confirmed = True
    request = CourseRequest(user_id=job.user_id, prompt=job.prompt)
    tasks.add_task(run_generation, job_id, request, state)
    return JobAccepted(
        job_id=job.id,
        status=JobStatus.RUNNING,
        status_url=f"/courses/{job.id}/progress",
    )


@router.get("/{job_id}/progress")
async def get_progress(job_id: str, learner: CurrentLearner) -> JobProgress:
    job = await job_store.get(job_id, learner.user_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return JobProgress(
        job_id=job.id,
        status=job.status,
        step=job.step,
        percent=job.percent,
        detail=job.detail,
        options=job.options,
        subject_name=job.subject_name,
        subject_description=job.subject_description,
        subject_sources=job.subject_sources,
        error=job.error,
        course_id=job.course_id,
    )


@router.get("")
async def list_courses(learner: CurrentLearner, limit: int = 10) -> list[CourseSummary]:
    """Newest first, so a client can answer "which course am I on" without keeping state."""
    return [
        CourseSummary(
            course_id=course.id,
            title=course.state.curriculum.title if course.state.curriculum else "",
            chapters=len(course.state.chapters),
            created_at=course.created_at,
        )
        for course in await course_store.for_user(learner.user_id, limit)
    ]


@router.get("/{course_id}")
async def get_course(course_id: str, learner: CurrentLearner) -> CourseDocument:
    # Not found and not yours are the same answer, so an id cannot be confirmed by probing.
    course = await course_store.get(course_id, learner.user_id)
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return as_document(course)
