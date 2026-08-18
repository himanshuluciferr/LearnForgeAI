"""Where course progress is kept. Interface-first, so Cosmos is a drop-in for the local store."""

from __future__ import annotations

import asyncio
from typing import Protocol

from azure.cosmos.exceptions import CosmosResourceNotFoundError

from backend.models.progress import CourseProgress
from backend.services.cosmos import PROGRESS, cosmos_enabled, get_container, to_document


class ProgressStore(Protocol):
    async def get(self, course_id: str, user_id: str) -> CourseProgress | None: ...

    async def save(self, progress: CourseProgress) -> CourseProgress: ...


class InMemoryProgressStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], CourseProgress] = {}
        self._lock = asyncio.Lock()

    async def get(self, course_id: str, user_id: str) -> CourseProgress | None:
        async with self._lock:
            return self._rows.get((user_id, course_id))

    async def save(self, progress: CourseProgress) -> CourseProgress:
        async with self._lock:
            self._rows[(progress.user_id, progress.course_id)] = progress
        return progress


class CosmosProgressStore:
    async def get(self, course_id: str, user_id: str) -> CourseProgress | None:
        container = get_container(PROGRESS)
        try:
            document = await container.read_item(course_id, partition_key=user_id)
        except CosmosResourceNotFoundError:
            return None
        return CourseProgress.model_validate(document)

    async def save(self, progress: CourseProgress) -> CourseProgress:
        container = get_container(PROGRESS)
        await container.upsert_item(to_document(progress))
        return progress


progress_store: ProgressStore = (
    CosmosProgressStore() if cosmos_enabled() else InMemoryProgressStore()
)
