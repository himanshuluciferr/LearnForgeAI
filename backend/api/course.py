"""Course generation and retrieval endpoints. Runs the Agent Framework workflow."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from backend.models.course import StoredCourse
from backend.models.job import GenerationJob
from backend.schemas.course import CourseRequest, JobAccepted, JobProgress
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
    return JobAccepted(
        job_id=job.id,
        status=job.status,
        status_url=f"/courses/{job.id}/progress",
    )


@router.get("/{job_id}/progress")
async def get_progress(job_id: str) -> JobProgress:
    job = await job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return JobProgress(
        job_id=job.id,
        status=job.status,
        step=job.step,
        percent=job.percent,
        detail=job.detail,
        error=job.error,
        course_id=job.course_id,
    )


@router.get("/{course_id}")
async def get_course(course_id: str) -> StoredCourse:
    course = await course_store.get(course_id)
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course
