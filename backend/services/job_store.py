"""Generation job persistence.

Two implementations behind one Protocol: Cosmos when an endpoint is configured, an
in-memory dict otherwise, so local development and tests need no Azure at all.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Protocol

from azure.cosmos.exceptions import CosmosResourceNotFoundError

from backend.models.job import GenerationJob, JobStatus
from backend.services.cosmos import JOBS, cosmos_enabled, get_container, to_document
from backend.workflow.state import WorkflowStep


def _apply(job: GenerationJob, updates: dict[str, Any]) -> GenerationJob:
    for field, value in updates.items():
        if value is not None:
            setattr(job, field, value)
    job.updated_at = datetime.now(timezone.utc)
    return job


class JobStore(Protocol):
    async def create(self, job: GenerationJob) -> GenerationJob: ...

    async def get(self, job_id: str, user_id: str | None = None) -> GenerationJob | None: ...

    async def for_user(self, user_id: str, limit: int = 5) -> list[GenerationJob]: ...

    async def update(
        self,
        job_id: str,
        *,
        user_id: str | None = None,
        status: JobStatus | None = None,
        step: WorkflowStep | None = None,
        percent: int | None = None,
        detail: str | None = None,
        options: list[str] | None = None,
        subject_name: str | None = None,
        subject_description: str | None = None,
        subject_sources: list[str] | None = None,
        error: str | None = None,
        course_id: str | None = None,
    ) -> GenerationJob | None: ...


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, GenerationJob] = {}
        self._lock = asyncio.Lock()

    async def create(self, job: GenerationJob) -> GenerationJob:
        async with self._lock:
            self._jobs[job.id] = job
        return job

    async def get(self, job_id: str, user_id: str | None = None) -> GenerationJob | None:
        async with self._lock:
            return self._match(job_id, user_id)

    def _match(self, job_id: str, user_id: str | None) -> GenerationJob | None:
        """Mirrors a Cosmos point read, which cannot reach into another user's partition."""
        job = self._jobs.get(job_id)
        if job is None or (user_id is not None and job.user_id != user_id):
            return None
        return job

    async def for_user(self, user_id: str, limit: int = 5) -> list[GenerationJob]:
        async with self._lock:
            mine = [job for job in self._jobs.values() if job.user_id == user_id]
        mine.sort(key=lambda job: job.updated_at, reverse=True)
        return mine[:limit]

    async def update(
        self,
        job_id: str,
        *,
        user_id: str | None = None,
        status: JobStatus | None = None,
        step: WorkflowStep | None = None,
        percent: int | None = None,
        detail: str | None = None,
        options: list[str] | None = None,
        subject_name: str | None = None,
        subject_description: str | None = None,
        subject_sources: list[str] | None = None,
        error: str | None = None,
        course_id: str | None = None,
    ) -> GenerationJob | None:
        async with self._lock:
            job = self._match(job_id, user_id)
            if job is None:
                return None
            return _apply(
                job,
                {
                    "status": status,
                    "step": step,
                    "percent": percent,
                    "detail": detail,
                    "options": options,
                    "subject_name": subject_name,
                    "subject_description": subject_description,
                    "subject_sources": subject_sources,
                    "error": error,
                    "course_id": course_id,
                },
            )


class CosmosJobStore:
    """Partitioned by user_id, so a read that knows the user costs one point read."""

    async def create(self, job: GenerationJob) -> GenerationJob:
        await get_container(JOBS).create_item(to_document(job))
        return job

    async def get(self, job_id: str, user_id: str | None = None) -> GenerationJob | None:
        container = get_container(JOBS)
        if user_id is not None:
            try:
                document = await container.read_item(job_id, partition_key=user_id)
            except CosmosResourceNotFoundError:
                return None
        else:
            # Without the partition key this fans out across every partition. Callers on the
            # progress-polling path pass user_id precisely to avoid paying that repeatedly.
            found = [
                item
                async for item in container.query_items(
                    "SELECT * FROM c WHERE c.id = @id",
                    parameters=[{"name": "@id", "value": job_id}],
                )
            ]
            if not found:
                return None
            document = found[0]
        # Cosmos adds _rid/_ts/_etag; pydantic ignores unknown keys, so they fall away here.
        return GenerationJob.model_validate(document)

    async def for_user(self, user_id: str, limit: int = 5) -> list[GenerationJob]:
        found = [
            item
            async for item in get_container(JOBS).query_items(
                "SELECT * FROM c ORDER BY c.updated_at DESC OFFSET 0 LIMIT @limit",
                parameters=[{"name": "@limit", "value": limit}],
                partition_key=user_id,
            )
        ]
        return [GenerationJob.model_validate(item) for item in found]

    async def update(
        self,
        job_id: str,
        *,
        user_id: str | None = None,
        status: JobStatus | None = None,
        step: WorkflowStep | None = None,
        percent: int | None = None,
        detail: str | None = None,
        options: list[str] | None = None,
        subject_name: str | None = None,
        subject_description: str | None = None,
        subject_sources: list[str] | None = None,
        error: str | None = None,
        course_id: str | None = None,
    ) -> GenerationJob | None:
        job = await self.get(job_id, user_id)
        if job is None:
            return None
        # Read-modify-write is safe here because one job is only ever written by its own runner.
        _apply(
            job,
            {
                "status": status,
                "step": step,
                "percent": percent,
                "detail": detail,
                "options": options,
                "subject_name": subject_name,
                "subject_description": subject_description,
                "subject_sources": subject_sources,
                "error": error,
                "course_id": course_id,
            },
        )
        await get_container(JOBS).replace_item(job_id, to_document(job))
        return job


job_store: JobStore = CosmosJobStore() if cosmos_enabled() else InMemoryJobStore()
