"""Generated course persistence.

JSON files under generated_courses/ for local development, which also makes each
agent's output readable on disk. The Cosmos-backed implementation lands in
cosmos.py; callers depend only on these methods.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

from backend.models.course import StoredCourse

COURSES_DIR = Path(__file__).resolve().parents[2] / "generated_courses"


def _is_safe_id(course_id: str) -> bool:
    """Ids arrive from the URL, so anything but a UUID could escape the courses directory."""
    try:
        UUID(course_id)
    except ValueError:
        return False
    return True


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

    async def get(self, course_id: str) -> StoredCourse | None:
        if not _is_safe_id(course_id):
            return None

        def read() -> str | None:
            path = self._path(course_id)
            return path.read_text(encoding="utf-8") if path.is_file() else None

        raw = await asyncio.to_thread(read)
        return StoredCourse.model_validate_json(raw) if raw is not None else None


course_store = FileCourseStore()
