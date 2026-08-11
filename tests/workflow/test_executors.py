"""Tests for the deterministic executors: rejection, clarification and publishing."""

from __future__ import annotations

import pytest

from backend.workflow import executors as executors_mod
from backend.workflow.executors import (
    MARKDOWN_FILENAME,
    MISSING_SKILL_MESSAGE,
    ClarifyExecutor,
    PublisherExecutor,
    build_clarification,
    choice_message,
)
from backend.workflow.state import (
    Chapter,
    ChapterOutline,
    Clarification,
    CourseState,
    Curriculum,
    ExperienceLevel,
    LearningRequest,
    MissingRequirement,
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


# --- the learner named several skills and chose none ---


def asking_about(*skills: str) -> CourseState:
    state = CourseState(
        job_id="job-1",
        user_id="user-1",
        prompt="teach me one of these",
        request=LearningRequest(
            is_learning_request=True, skill=skills[0], alternatives=list(skills)
        ),
    )
    state.mark(WorkflowStep.REQUIREMENT)
    return state


@pytest.mark.asyncio
async def test_the_choice_is_handed_back_as_data_not_only_as_a_sentence():
    """A card needs the options themselves; re-parsing them out of prose is a second bug."""
    ctx = RecordingContext()

    await ClarifyExecutor(id="clarify").run(asking_about("React", "Vue"), ctx)

    assert isinstance(ctx.outputs[0], Clarification)
    assert ctx.outputs[0].options == ["React", "Vue"]


@pytest.mark.asyncio
async def test_the_question_names_every_option_the_learner_gave():
    ctx = RecordingContext()

    await ClarifyExecutor(id="clarify").run(asking_about("Terraform", "Bicep", "Pulumi"), ctx)

    message = ctx.outputs[0].message
    assert "Terraform" in message and "Bicep" in message and "Pulumi" in message


@pytest.mark.asyncio
async def test_nothing_is_generated_before_the_learner_answers():
    """The whole point of stopping here is that no chapter has been paid for yet."""
    state = asking_about("React", "Vue")

    await ClarifyExecutor(id="clarify").run(state, RecordingContext())

    assert state.curriculum is None
    assert state.chapters == []
    assert state.percent == 5


def test_two_options_read_as_a_pair_rather_than_a_list():
    assert choice_message(["React", "Vue"]).startswith("You mentioned React and Vue,")


def test_three_options_are_separated_before_the_last_one():
    assert choice_message(["a", "b", "c"]).startswith("You mentioned a, b and c,")


def test_a_request_too_broad_to_build_on_is_asked_for_a_skill_not_for_a_choice():
    """'Teach me Microsoft stuff' offers nothing to choose between, so listing options
    would mean inventing them — the guess this node exists to refuse."""
    clarification = build_clarification(
        LearningRequest(is_learning_request=True, missing_requirements=[MissingRequirement.SKILL])
    )

    assert clarification.message == MISSING_SKILL_MESSAGE
    assert clarification.options == []


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
