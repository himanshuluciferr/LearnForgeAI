"""Request and response models for taking a quiz.

The answer never travels outward. `QuizQuestionOut` has no field to hold `correct_index`, so
it cannot be leaked by forgetting to strip it — the same move as `PracticeKind` having no
multiple-choice member: the mistake is unrepresentable rather than guarded against.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class QuizQuestionOut(BaseModel):
    number: int
    question: str
    options: list[str]


class QuizOut(BaseModel):
    course_id: str
    scope: str
    chapter_number: int | None = None
    questions: list[QuizQuestionOut]


class AnswerSubmission(BaseModel):
    """Chosen option index per question number. A question left out is unanswered, which is
    marked wrong rather than skipped: a score has to be out of the whole quiz."""

    answers: dict[int, int] = Field(default_factory=dict)


class MarkedAnswer(BaseModel):
    number: int
    chosen_index: int | None
    correct_index: int
    correct: bool
    explanation: str = ""


class QuizResult(BaseModel):
    course_id: str
    chapter_number: int | None = None
    correct: int
    total: int
    percent: int
    answers: list[MarkedAnswer]
