"""Request and response models for the mentor."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MentorQuestion(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=2, max_length=1000)


class MentorReply(BaseModel):
    course_id: str
    question: str
    answer: str
    # False when the course does not cover it, which is a real answer rather than a failure.
    grounded: bool
    chapter_number: int | None = None
