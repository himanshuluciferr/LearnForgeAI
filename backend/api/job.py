"""Job listing.

Its own router rather than a path under /courses, where `GET /jobs` would have to be declared
before `/courses/{course_id}` to avoid being read as a course whose id is "jobs".
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.api.deps import CurrentLearner
from backend.schemas.course import JobProgress
from backend.services.job_store import job_store

router = APIRouter(prefix="/jobs", tags=["job"])


@router.get("")
async def list_jobs(learner: CurrentLearner, limit: int = 5) -> list[JobProgress]:
    """Most recently touched first, so a client can find the run it is waiting on without
    having kept the id."""
    return [
        JobProgress(
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
        for job in await job_store.for_user(learner.user_id, limit)
    ]
