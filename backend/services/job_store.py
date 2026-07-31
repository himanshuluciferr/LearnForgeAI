"""Generation job persistence.

In-memory implementation for local development. The Cosmos-backed implementation
lands in cosmos.py once the account exists; endpoints depend only on these methods.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from backend.models.job import GenerationJob, JobStatus
from backend.workflow.state import WorkflowStep


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, GenerationJob] = {}
        self._lock = asyncio.Lock()

    async def create(self, job: GenerationJob) -> GenerationJob:
        async with self._lock:
            self._jobs[job.id] = job
        return job

    async def get(self, job_id: str) -> GenerationJob | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def update(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        step: WorkflowStep | None = None,
        percent: int | None = None,
        detail: str | None = None,
        error: str | None = None,
        course_id: str | None = None,
    ) -> GenerationJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            updates = {
                "status": status,
                "step": step,
                "percent": percent,
                "detail": detail,
                "error": error,
                "course_id": course_id,
            }
            for field, value in updates.items():
                if value is not None:
                    setattr(job, field, value)
            job.updated_at = datetime.now(timezone.utc)
            return job


job_store = InMemoryJobStore()
