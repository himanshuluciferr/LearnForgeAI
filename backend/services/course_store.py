"""Generated course persistence.

Cosmos when an endpoint is configured, otherwise JSON files under generated_courses/,
which also keeps every agent's output readable on disk during development.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol
from uuid import UUID

from azure.cosmos.exceptions import CosmosResourceNotFoundError

from backend.models.course import StoredCourse
from backend.services.cosmos import COURSES, cosmos_enabled, get_container, to_document

COURSES_DIR = Path(__file__).resolve().parents[2] / "generated_courses"


def _is_safe_id(course_id: str) -> bool:
    """Ids arrive from the URL, so anything but a UUID could escape the courses directory."""
    try:
        UUID(course_id)
    except ValueError:
        return False
    return True


class CourseStore(Protocol):
    async def save(self, course: StoredCourse) -> StoredCourse: ...

    async def get(self, course_id: str, user_id: str | None = None) -> StoredCourse | None: ...

    async def for_user(self, user_id: str, limit: int = 10) -> list[StoredCourse]: ...


class FileCourseStore:
    def __init__(self, directory: Path = COURSES_DIR) -> None:
        self._dir = directory

    def _path(self, course_id: str) -> Path:
        return self._dir / f"{course_id}.json"

    async def save(self, course: StoredCourse) -> StoredCourse:
        # File IO is blocking, so it runs off the event loop.
        def write() -> None:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._path(course.id).write_text(course.model_dump_json(indent=2), encoding="utf-8")

        await asyncio.to_thread(write)
        return course

    async def get(self, course_id: str, user_id: str | None = None) -> StoredCourse | None:
        if not _is_safe_id(course_id):
            return None

        def read() -> str | None:
            path = self._path(course_id)
            return path.read_text(encoding="utf-8") if path.is_file() else None

        raw = await asyncio.to_thread(read)
        if raw is None:
            return None
        course = StoredCourse.model_validate_json(raw)
        # Mirrors a Cosmos point read, which cannot reach into another user's partition.
        return None if user_id is not None and course.user_id != user_id else course

    async def for_user(self, user_id: str, limit: int = 10) -> list[StoredCourse]:
        def read_all() -> list[StoredCourse]:
            found = []
            for path in self._dir.glob("*.json"):
                try:
                    course = StoredCourse.model_validate_json(path.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                if course.user_id == user_id:
                    found.append(course)
            return found

        courses = await asyncio.to_thread(read_all)
        courses.sort(key=lambda course: course.created_at, reverse=True)
        return courses[:limit]


class CosmosCourseStore:
    """Partitioned by user_id, matching jobs, so one learner's data lives on one partition."""

    async def save(self, course: StoredCourse) -> StoredCourse:
        # upsert rather than create: a regenerated course keeps its id.
        await get_container(COURSES).upsert_item(to_document(course))
        return course

    async def get(self, course_id: str, user_id: str | None = None) -> StoredCourse | None:
        container = get_container(COURSES)
        if user_id is not None:
            try:
                document = await container.read_item(course_id, partition_key=user_id)
            except CosmosResourceNotFoundError:
                return None
        else:
            found = [
                item
                async for item in container.query_items(
                    "SELECT * FROM c WHERE c.id = @id",
                    parameters=[{"name": "@id", "value": course_id}],
                )
            ]
            if not found:
                return None
            document = found[0]
        return StoredCourse.model_validate(document)

    async def for_user(self, user_id: str, limit: int = 10) -> list[StoredCourse]:
        container = get_container(COURSES)
        # The (user_id, created_at DESC) composite index exists for exactly this query.
        found = [
            item
            async for item in container.query_items(
                "SELECT * FROM c ORDER BY c.created_at DESC OFFSET 0 LIMIT @limit",
                parameters=[{"name": "@limit", "value": limit}],
                partition_key=user_id,
            )
        ]
        return [StoredCourse.model_validate(item) for item in found]


course_store: CourseStore = CosmosCourseStore() if cosmos_enabled() else FileCourseStore()
