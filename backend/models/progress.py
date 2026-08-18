"""What a learner has worked through on one course.

One document per learner per course, keyed on the course id and partitioned by user_id, so a
read is a point read. Cosmos partition key is `user_id`, matching every other container.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CourseProgress(BaseModel):
    id: str
    user_id: str
    course_id: str
    # Chapter numbers, unique and sorted. A set would not survive the JSON round trip.
    read_chapters: list[int] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=_now)
