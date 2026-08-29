"""Turning a finished course into indexable passages.

Chunked with the same splitter the lexical selector uses, so the two are answering from the
same units and a comparison between them means something.
"""

from __future__ import annotations

import hashlib
import logging

from backend.services import ai_search
from backend.skills.passages.skill import split
from backend.workflow.state import CourseState

logger = logging.getLogger(__name__)


def passage_id(course_id: str, url: str, order: int) -> str:
    """A search key allows letters, digits, dash, underscore and equals, and urls are none of
    those. Hashed rather than sanitised so two urls cannot collapse to one key."""
    digest = hashlib.sha256(f"{course_id}|{url}".encode()).hexdigest()[:24]
    return f"{digest}-{order}"


def documents(course_id: str, user_id: str, state: CourseState) -> list[dict]:
    """The course and the pages it was written from, as one flat set of passages.

    `chapter_number` rides along so an answer can say where to re-read; it is absent for a
    source, which is exactly the distinction the mentor already draws.
    """
    rows: list[dict] = []
    for chapter in state.chapters:
        for order, text in enumerate(split(chapter.body_markdown)):
            rows.append(
                {
                    "id": passage_id(course_id, f"chapter-{chapter.number}", order),
                    "user_id": user_id,
                    "course_id": course_id,
                    "chapter_number": chapter.number,
                    "url": f"chapter-{chapter.number}",
                    "title": f"Chapter {chapter.number}: {chapter.title}",
                    "text": text,
                }
            )
    for source in state.research:
        for order, text in enumerate(split(source.text)):
            rows.append(
                {
                    "id": passage_id(course_id, source.url, order),
                    "user_id": user_id,
                    "course_id": course_id,
                    "chapter_number": None,
                    "url": source.url,
                    "title": source.title,
                    "text": text,
                }
            )
    return rows


async def index_course(course_id: str, user_id: str, state: CourseState) -> int:
    """Best effort: a course that fails to index is still a course. The mentor falls back to
    lexical for anything the index does not hold, so this failing costs speed, not answers."""
    if not ai_search.search_enabled():
        return 0
    rows = documents(course_id, user_id, state)
    if not rows:
        return 0
    try:
        await ai_search.ensure_index()
        # Dropped first because a regenerated course keeps its id, and its old passages would
        # otherwise stay searchable beside the new ones.
        await ai_search.drop_course(course_id, user_id)
        if ai_search.vectors_enabled():
            from backend.services.embeddings import embed

            vectors = await embed([row["text"] for row in rows])
            for row, vector in zip(rows, vectors):
                row["vector"] = vector
        written = await ai_search.upload(rows)
        logger.info("ai-search: indexed %d passages for course %s", written, course_id)
        return written
    except Exception:
        logger.exception("ai-search: indexing course %s failed", course_id)
        return 0
