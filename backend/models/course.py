"""Persisted course — the complete output of one generation run.

Cosmos partition key is `user_id`, matching GenerationJob.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from backend.workflow.state import CourseState


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StoredCourse(BaseModel):
    id: str
    user_id: str
    job_id: str
    # The whole workflow state is kept verbatim so every agent's output stays inspectable.
    state: CourseState
    created_at: datetime = Field(default_factory=_now)
