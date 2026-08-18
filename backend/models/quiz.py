"""A learner's attempt at one quiz.

Cosmos partition key is `user_id`, matching GenerationJob and StoredCourse.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from backend.schemas.quiz import MarkedAnswer


def _now() -> datetime:
    return datetime.now(timezone.utc)


class QuizAttempt(BaseModel):
    id: str
    user_id: str
    course_id: str
    chapter_number: int | None = None
    correct: int
    total: int
    percent: int
    answers: list[MarkedAnswer] = Field(default_factory=list)
    # `created_at` rather than `taken_at`: quiz_results shares the jobs index policy, which
    # indexes created_at and excludes everything else, and Cosmos refuses to order by a path
    # its policy excludes.
    created_at: datetime = Field(default_factory=_now)
