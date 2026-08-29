"""Learner progress tracking endpoints.

Progress is stored as the bare fact — which chapters have been read — and every number a
caller sees is derived from it. A stored percentage would be free to disagree with the
chapters it was counted from.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from backend.api.deps import CurrentLearner
from backend.models.course import StoredCourse
from backend.models.progress import CourseProgress
from backend.schemas.progress import ChapterProgress, ProgressOut
from backend.services.course_store import course_store
from backend.services.progress_store import progress_store
from backend.services.quiz_store import quiz_store

router = APIRouter(prefix="/progress", tags=["progress"])


async def load_course(course_id: str, user_id: str) -> StoredCourse:
    course = await course_store.get(course_id, user_id)
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


async def summarise(course: StoredCourse, user_id: str) -> ProgressOut:
    stored = await progress_store.get(course.id, user_id)
    read = set(stored.read_chapters) if stored else set()
    best: dict[int | None, int] = {}
    for attempt in await quiz_store.for_course(course.id, user_id):
        best[attempt.chapter_number] = max(best.get(attempt.chapter_number, 0), attempt.percent)

    chapters = [
        ChapterProgress(
            number=chapter.number,
            title=chapter.title,
            read=chapter.number in read,
            best_quiz_percent=best.get(chapter.number),
        )
        for chapter in course.state.chapters
    ]
    done = sum(1 for chapter in chapters if chapter.read)
    curriculum = course.state.curriculum
    published = course.state.published
    return ProgressOut(
        course_id=course.id,
        title=curriculum.title if curriculum else "",
        chapters_read=done,
        chapters_total=len(chapters),
        percent=round(100 * done / len(chapters)) if chapters else 0,
        next_chapter=next((c.number for c in chapters if not c.read), None),
        markdown_url=published.markdown_url if published else None,
        chapters=chapters,
    )


@router.get("/{course_id}")
async def get_progress(course_id: str, learner: CurrentLearner) -> ProgressOut:
    course = await load_course(course_id, learner.user_id)
    return await summarise(course, learner.user_id)


@router.put("/{course_id}/chapters/{number}")
async def mark_chapter_read(
    course_id: str, number: int, learner: CurrentLearner
) -> ProgressOut:
    """Idempotent: finishing a chapter twice is something a learner does, not an error."""
    user_id = learner.user_id
    course = await load_course(course_id, user_id)
    if not any(chapter.number == number for chapter in course.state.chapters):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Course has no chapter {number}"
        )

    stored = await progress_store.get(course_id, user_id) or CourseProgress(
        id=course_id, user_id=user_id, course_id=course_id
    )
    stored.read_chapters = sorted(set(stored.read_chapters) | {number})
    await progress_store.save(stored)
    return await summarise(course, user_id)
