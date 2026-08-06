"""Persisted generation job — one row per course-generation run.

Cosmos partition key is `user_id`; progress reads are always scoped to one user.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field

from backend.workflow.state import WorkflowStep


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    # The prompt wasn't a learning request. Not an error, so it is kept apart from FAILED.
    REJECTED = "rejected"
    # The learner named several skills and chose none. Answerable by asking again.
    NEEDS_CHOICE = "needs-choice"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GenerationJob(BaseModel):
    id: str
    user_id: str
    prompt: str
    status: JobStatus = JobStatus.QUEUED
    step: WorkflowStep | None = None
    percent: int = 0
    detail: str | None = None
    # Kept as data rather than only inside `detail`, so a card can offer them as buttons.
    options: list[str] = Field(default_factory=list)
    error: str | None = None
    course_id: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
