"""Tests for the deterministic executors: rejection and publishing."""

from __future__ import annotations

import pytest

from backend.workflow import executors as executors_mod
from backend.workflow.executors import MARKDOWN_FILENAME, PublisherExecutor
from backend.workflow.state import (
    Chapter,
    ChapterOutline,
    CourseState,
    Curriculum,
    ExperienceLevel,
    LearningRequest,
    WorkflowStep,
)

CURRICULUM = Curriculum(
    title="Git",
    summary="s",
    chapters=[ChapterOutline(number=1, title="t", objectives=["a"])],
)
REQUEST = LearningRequest(
    is_learning_request=True,
    skill="Git",
    experience=ExperienceLevel.BEGINNER,
    goal="g",
    daily_minutes=30,
)


class RecordingStore:
    def __init__(self) -> None:
        self.puts: list[tuple[str, str, str, str]] = []

    async def put(self, user_id: str, job_id: str, filename: str, content: str) -> str:
        self.puts.append((user_id, job_id, filename, content))
        return "https://example/course.md"


class RecordingContext:
    def __init__(self) -> None:
        self.outputs: list[CourseState] = []

    async def yield_output(self, value: CourseState) -> None:
        self.outputs.append(value)


@pytest.fixture
def store(monkeypatch):
    recorder = RecordingStore()
    monkeypatch.setattr(executors_mod, "artifact_store", recorder)
    return recorder


def finished_state() -> CourseState:
    return CourseState(
        job_id="job-1",
        user_id="user-1",
        prompt="teach me git",
        request=REQUEST,
        curriculum=CURRICULUM,
        chapters=[Chapter(number=1, title="t", body_markdown="body")],
    )


@pytest.mark.asyncio
async def test_publishing_stores_the_rendered_course_under_the_job(store):
    state = finished_state()

    await PublisherExecutor(id=WorkflowStep.PUBLISHER).run(state, RecordingContext())

    user_id, job_id, filename, content = store.puts[0]
    assert (user_id, job_id, filename) == ("user-1", "job-1", MARKDOWN_FILENAME)
    assert content.startswith("# Git")


@pytest.mark.asyncio
async def test_publishing_records_the_link_and_marks_the_course_complete(store):
    state = finished_state()

    await PublisherExecutor(id=WorkflowStep.PUBLISHER).run(state, RecordingContext())

    assert state.published is not None
    assert state.published.markdown_url == "https://example/course.md"
    assert state.percent == 3


@pytest.mark.asyncio
async def test_the_other_formats_stay_empty_rather_than_pointing_at_the_markdown(store):
    """A pdf_url that serves markdown is worse than no pdf_url."""
    state = finished_state()

    await PublisherExecutor(id=WorkflowStep.PUBLISHER).run(state, RecordingContext())

    assert state.published is not None
    assert state.published.pdf_url is None
    assert state.published.docx_url is None


@pytest.mark.asyncio
async def test_the_finished_course_is_what_the_workflow_hands_back(store):
    state = finished_state()
    ctx = RecordingContext()

    await PublisherExecutor(id=WorkflowStep.PUBLISHER).run(state, ctx)

    assert ctx.outputs == [state]


@pytest.mark.asyncio
async def test_nothing_is_stored_when_there_is_no_course_to_render(store):
    state = finished_state()
    state.chapters = []

    with pytest.raises(ValueError, match="no chapters"):
        await PublisherExecutor(id=WorkflowStep.PUBLISHER).run(state, RecordingContext())

    assert store.puts == []
