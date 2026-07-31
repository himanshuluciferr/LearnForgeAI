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

    assert request.skill == ""
    assert request.experience == ExperienceLevel.BEGINNER
    assert request.daily_minutes == 30
    assert request.language == "en"


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
