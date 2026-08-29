"""Generated course persistence.

Cosmos when an endpoint is configured, otherwise JSON files under generated_courses/,
which also keeps every agent's output readable on disk during development.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from azure.cosmos.exceptions import CosmosResourceNotFoundError
from pydantic import ValidationError

from backend.models.course import StoredCourse
from backend.services.cosmos import COURSES, cosmos_enabled, get_container, to_document
from backend.workflow.state import WorkflowStep

logger = logging.getLogger(__name__)

COURSES_DIR = Path(__file__).resolve().parents[2] / "generated_courses"

# Cosmos refuses to ORDER BY a path its index policy excludes. Named so a test can check the
# policy still covers it; the courses container also has a composite index on (user_id, this).
ORDER_FIELD = "created_at"

KNOWN_STEPS = {step.value for step in WorkflowStep}

# What a reader needs, and nothing else. Measured on a 20-chapter course: the stored document
# is 609 KB, of which the reviewer's verdicts and the quiz answer keys are 206 KB that the
# page never shows. The account is in East US and a course is large, so bytes not asked for
# are seconds not waited for.
DISPLAY_FIELDS = """
    c.id, c.user_id, c.job_id, c.created_at,
    c.state.curriculum, c.state.chapters, c.state.practice,
    c.state.projects, c.state.published,
    ARRAY(SELECT VALUE q.chapter_number FROM q IN c.state.quizzes) AS quiz_chapters,
    ARRAY_LENGTH(c.state.quizzes) AS quiz_count
"""

# The library shows a title and a chapter count. Reading whole courses to render that was
# hundreds of kilobytes per row.
SUMMARY_FIELDS = """
    c.id, c.created_at, c.state.curriculum.title,
    ARRAY_LENGTH(c.state.chapters) AS chapters
"""


def as_stored(row: dict[str, Any]) -> dict[str, Any]:
    """Reshapes a projected row back into the stored shape.

    Rebuilding the document rather than parsing the projection separately keeps one path
    into `StoredCourse`, so the display read cannot drift from the full one. Quizzes come
    back as their chapter numbers alone, which is all the page is told.
    """
    quizzes = [
        {"scope": "", "chapter_number": number, "questions": []}
        for number in row.get("quiz_chapters") or []
    ]
    # A final quiz has no chapter number, and Cosmos drops it from the array rather than
    # returning a null, so it is restored from the count.
    if len(quizzes) < (row.get("quiz_count") or 0):
        quizzes.append({"scope": "", "chapter_number": None, "questions": []})
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "job_id": row["job_id"],
        "created_at": row["created_at"],
        "state": {
            "job_id": row["job_id"],
            "user_id": row["user_id"],
            "prompt": "",
            "curriculum": row.get("curriculum"),
            "chapters": row.get("chapters") or [],
            "practice": row.get("practice") or [],
            "projects": row.get("projects") or [],
            "published": row.get("published"),
            "quizzes": quizzes,
        },
    }


def repair(document: dict[str, Any]) -> None:
    """Bring a course written by an earlier version up to the current shape.

    Measured against the live container: 4 of 37 stored courses no longer validate, because
    `research[].text` was once optional and `completed_steps` once held a step name that has
    since been retired. Neither field is anything the reader sees, so refusing the whole
    course over them loses a learner their library.

    Repairs on read rather than relaxing the model, so the guarantees still hold on write.
    """
    state = document.get("state")
    if not isinstance(state, dict):
        return
    for source in state.get("research") or []:
        if isinstance(source, dict):
            source.setdefault("text", "")
    steps = state.get("completed_steps")
    if isinstance(steps, list):
        state["completed_steps"] = [step for step in steps if step in KNOWN_STEPS]


def load(document: dict[str, Any]) -> StoredCourse | None:
    """None rather than raising: one unreadable course must not fail a whole library."""
    repair(document)
    try:
        return StoredCourse.model_validate(document)
    except ValidationError:
        logger.warning("course %s could not be read", document.get("id"), exc_info=True)
        return None


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

    async def for_display(self, course_id: str, user_id: str) -> StoredCourse | None: ...

    async def summaries(self, user_id: str, limit: int = 10) -> list[dict[str, Any]]: ...


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
        course = load(json.loads(raw))
        if course is None:
            return None
        # Mirrors a Cosmos point read, which cannot reach into another user's partition.
        return None if user_id is not None and course.user_id != user_id else course

    async def for_user(self, user_id: str, limit: int = 10) -> list[StoredCourse]:
        def read_all() -> list[StoredCourse]:
            found = []
            for path in self._dir.glob("*.json"):
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                course = load(document)
                if course is not None and course.user_id == user_id:
                    found.append(course)
            return found

        courses = await asyncio.to_thread(read_all)
        courses.sort(key=lambda course: course.created_at, reverse=True)
        return courses[:limit]

    async def for_display(self, course_id: str, user_id: str) -> StoredCourse | None:
        """No projection to make locally: reading the file already cost the whole file."""
        return await self.get(course_id, user_id)

    async def summaries(self, user_id: str, limit: int = 10) -> list[dict[str, Any]]:
        return [
            {
                "id": course.id,
                "created_at": course.created_at,
                "title": course.state.curriculum.title if course.state.curriculum else "",
                "chapters": len(course.state.chapters),
            }
            for course in await self.for_user(user_id, limit)
        ]


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
        return load(document)

    async def for_user(self, user_id: str, limit: int = 10) -> list[StoredCourse]:
        container = get_container(COURSES)
        # The (user_id, created_at DESC) composite index exists for exactly this query.
        found = [
            item
            async for item in container.query_items(
                f"SELECT * FROM c ORDER BY c.{ORDER_FIELD} DESC OFFSET 0 LIMIT @limit",
                parameters=[{"name": "@limit", "value": limit}],
                partition_key=user_id,
            )
        ]
        # One unreadable course used to raise here and fail the whole listing, while the file
        # store quietly skipped it.
        return [course for course in map(load, found) if course is not None]

    async def for_display(self, course_id: str, user_id: str) -> StoredCourse | None:
        found = [
            item
            async for item in get_container(COURSES).query_items(
                f"SELECT {DISPLAY_FIELDS} FROM c WHERE c.id = @id",  # noqa: S608 - fixed text
                parameters=[{"name": "@id", "value": course_id}],
                partition_key=user_id,
            )
        ]
        return load(as_stored(found[0])) if found else None

    async def summaries(self, user_id: str, limit: int = 10) -> list[dict[str, Any]]:
        return [
            item
            async for item in get_container(COURSES).query_items(
                f"SELECT {SUMMARY_FIELDS} FROM c ORDER BY c.{ORDER_FIELD} DESC "  # noqa: S608
                "OFFSET 0 LIMIT @limit",
                parameters=[{"name": "@limit", "value": limit}],
                partition_key=user_id,
            )
        ]


course_store: CourseStore = CosmosCourseStore() if cosmos_enabled() else FileCourseStore()
