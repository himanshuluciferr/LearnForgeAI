"""Course generation and retrieval endpoints. Runs the Agent Framework workflow."""

from __future__ import annotations

from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from backend.models.course import StoredCourse
from backend.models.job import GenerationJob, JobStatus
from backend.schemas.course import ChoiceRequest, CourseRequest, JobAccepted, JobProgress
from backend.services.course_store import course_store
from backend.services.job_store import job_store
from backend.workflow.runner import run_generation

router = APIRouter(prefix="/courses", tags=["course"])


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_course(request: CourseRequest, tasks: BackgroundTasks) -> JobAccepted:
    """Generation takes minutes, so the job is queued and the caller polls for progress."""
    job = GenerationJob(id=str(uuid4()), user_id=request.user_id, prompt=request.prompt)
    await job_store.create(job)
    tasks.add_task(run_generation, job.id, request)
    # Every later call carries user_id: it both routes to the partition and authorises the read.
    return JobAccepted(
        job_id=job.id,
        status=job.status,
        status_url=f"/courses/{job.id}/progress?user_id={quote(request.user_id)}",
    )


@router.post("/{job_id}/confirm", status_code=status.HTTP_202_ACCEPTED)
async def confirm_subject(
    job_id: str,
    tasks: BackgroundTasks,
    user_id: str,
    selection: ChoiceRequest | None = None,
) -> JobAccepted:
    """Answers a clarification or approves the identified subject."""
    job = await job_store.get(job_id, user_id)
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
            status_url=f"/courses/{job.id}/progress?user_id={quote(job.user_id)}",
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
        status_url=f"/courses/{job.id}/progress?user_id={quote(job.user_id)}",
    )


@router.get("/{job_id}/progress")
async def get_progress(job_id: str, user_id: str) -> JobProgress:
    job = await job_store.get(job_id, user_id)
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


@router.get("/{course_id}")
async def get_course(course_id: str, user_id: str) -> StoredCourse:
    # Not found and not yours are the same answer, so an id cannot be confirmed by probing.
    course = await course_store.get(course_id, user_id)
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course
