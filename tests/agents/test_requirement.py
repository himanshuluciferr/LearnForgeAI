"""Offline tests for requirement-agent: schema contract, executor wiring, routing."""

import pytest
from pydantic import ValidationError

from backend.agents import requirement as requirement_module
from backend.agents.requirement import RequirementExecutor
from backend.workflow.state import (
    STEP_WEIGHTS,
    CourseState,
    ExperienceLevel,
    LearningRequest,
    MissingRequirement,
    StatedExperience,
    WorkflowStep,
)
from backend.workflow.workflow import _is_not_learning_request


class CapturingContext:
    """Stand-in for WorkflowContext; the executor only ever calls send_message."""

    def __init__(self) -> None:
        self.messages: list[CourseState] = []

    async def send_message(self, message: CourseState) -> None:
        self.messages.append(message)


def test_only_is_learning_request_is_required():
    """Every other field defaults, so an off-topic prompt needs no invented values."""
    request = LearningRequest(is_learning_request=False)

    assert request.skill is None
    assert request.experience == StatedExperience.UNKNOWN
    assert request.experience_evidence is None
    assert request.goal is None
    assert request.daily_minutes is None
    assert request.language == "en"
    assert request.missing_requirements == []


def test_an_unstated_level_is_recorded_as_unknown_and_taught_as_beginner():
    """The message said nothing, so the schema says nothing — but a course still needs a level,
    and every node reads the same fallback instead of inventing its own."""
    request = LearningRequest(is_learning_request=True, skill="Rust")

    assert request.experience is StatedExperience.UNKNOWN
    assert request.assumed_level is ExperienceLevel.BEGINNER


def test_a_stated_level_survives_the_fallback():
    request = LearningRequest(
        is_learning_request=True, skill="Rust", experience=StatedExperience.ADVANCED
    )

    assert request.assumed_level is ExperienceLevel.ADVANCED


def test_an_unstated_time_commitment_still_yields_a_usable_number():
    assert LearningRequest(is_learning_request=True, skill="Rust").minutes_per_day == 30
    assert (
        LearningRequest(is_learning_request=True, skill="Rust", daily_minutes=45).minutes_per_day
        == 45
    )


def test_missing_requirements_are_a_closed_set():
    """Routing keys off these values, so a free-form string would silently never match."""
    assert list(MissingRequirement) == [
        MissingRequirement.SKILL,
        MissingRequirement.SKILL_CHOICE,
    ]


def test_an_unknown_missing_requirement_is_rejected():
    with pytest.raises(ValidationError):
        LearningRequest(is_learning_request=True, missing_requirements=["budget"])


@pytest.mark.parametrize("minutes", [4, 481])
def test_daily_minutes_outside_bounds_is_rejected(minutes):
    with pytest.raises(ValidationError):
        LearningRequest(is_learning_request=True, daily_minutes=minutes)


@pytest.mark.asyncio
async def test_executor_fills_state_and_forwards_it(monkeypatch):
    extracted = LearningRequest(is_learning_request=True, skill="Kubernetes operators")

    async def fake_extract(prompt: str) -> LearningRequest:
        assert prompt == "Teach me Kubernetes operators"
        return extracted

    monkeypatch.setattr(requirement_module, "extract_requirement", fake_extract)
    state = CourseState(job_id="j1", user_id="u1", prompt="Teach me Kubernetes operators")
    ctx = CapturingContext()

    await RequirementExecutor(id=WorkflowStep.REQUIREMENT).run(state, ctx)

    assert state.request is extracted
    assert state.completed_steps == [WorkflowStep.REQUIREMENT]
    assert state.percent == STEP_WEIGHTS[WorkflowStep.REQUIREMENT]
    # The same object travels the edge; MAF passes state by reference.
    assert ctx.messages == [state]


@pytest.mark.asyncio
async def test_executor_marks_the_step_only_once(monkeypatch):
    """The review loop re-runs steps, so percent must not creep past 100."""

    async def fake_extract(prompt: str) -> LearningRequest:
        return LearningRequest(is_learning_request=True, skill="Go")

    monkeypatch.setattr(requirement_module, "extract_requirement", fake_extract)
    state = CourseState(job_id="j1", user_id="u1", prompt="Teach me Go")
    executor = RequirementExecutor(id=WorkflowStep.REQUIREMENT)

    await executor.run(state, CapturingContext())
    await executor.run(state, CapturingContext())

    assert state.completed_steps == [WorkflowStep.REQUIREMENT]


@pytest.mark.parametrize(
    ("request_", "routes_to_rejection"),
    [
        (None, False),
        (LearningRequest(is_learning_request=True, skill="Rust"), False),
        (LearningRequest(is_learning_request=False), True),
    ],
    ids=["not-yet-extracted", "learning-request", "off-topic"],
)
def test_rejection_edge_fires_only_for_off_topic_prompts(request_, routes_to_rejection):
    state = CourseState(job_id="j1", user_id="u1", prompt="p", request=request_)

    assert _is_not_learning_request(state) is routes_to_rejection
