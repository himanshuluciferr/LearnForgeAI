"""Request and response models for the course endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.models.job import JobStatus
from backend.workflow.state import WorkflowStep


class CourseRequest(BaseModel):
    """Raw Teams prompt. requirement-agent parses it into a LearningRequest."""

    user_id: str
    prompt: str = Field(min_length=3, max_length=2000)
    language: str = "en"


class JobAccepted(BaseModel):
    job_id: str
    status: JobStatus
    status_url: str


class JobProgress(BaseModel):
    job_id: str
    status: JobStatus
    step: WorkflowStep | None = None
    percent: int = 0
    detail: str | None = None
    options: list[str] = Field(default_factory=list)
    error: str | None = None
    # Set once the course is saved; this is how a poller finds the result.
    course_id: str | None = None
