"""Request and response models for the course endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.models.job import JobStatus
from backend.workflow.state import WorkflowStep


class CourseRequest(BaseModel):
    """Raw Teams prompt. requirement-agent parses it into a LearningRequest."""

    user_id: str
    prompt: str = Field(min_length=3, max_length=2000)
    language: str = "en"


class ChoiceRequest(BaseModel):
    """The option selected in response to a needs-choice job."""

    model_config = ConfigDict(str_strip_whitespace=True)

    choice: str = Field(min_length=1)


class JobAccepted(BaseModel):
    job_id: str
    status: JobStatus
    status_url: str


class CourseSummary(BaseModel):
    """Enough to list and choose a course without shipping the whole state, which runs to
    hundreds of kilobytes."""

    course_id: str
    title: str
    chapters: int
    created_at: datetime


class JobProgress(BaseModel):
    job_id: str
    status: JobStatus
    step: WorkflowStep | None = None
    percent: int = 0
    detail: str | None = None
    options: list[str] = Field(default_factory=list)
    # Populated while the job waits on NEEDS_CONFIRMATION, so the card needs no second lookup.
    subject_name: str | None = None
    subject_description: str | None = None
    subject_sources: list[str] = Field(default_factory=list)
    error: str | None = None
    # Set once the course is saved; this is how a poller finds the result.
    course_id: str | None = None
