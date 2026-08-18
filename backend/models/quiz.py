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
    taken_at: datetime = Field(default_factory=_now)
