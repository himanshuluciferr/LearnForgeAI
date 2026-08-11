"""Offline tests for skill-analysis-agent: prompt building, executor wiring, routing."""

import pytest
from pydantic import ValidationError

from backend.agents import skill_analysis as skill_module
from backend.agents.skill_analysis import SkillAnalysisExecutor, build_prompt
from backend.workflow.state import (
    STEP_WEIGHTS,
    CourseState,
    ExperienceLevel,
    LearningRequest,
    MissingRequirement,
    SkillAnalysis,
    WorkflowStep,
)
from backend.workflow.workflow import _is_not_learning_request, _needs_clarification


class CapturingContext:
    def __init__(self) -> None:
        self.messages: list[CourseState] = []

    async def send_message(self, message: CourseState) -> None:
        self.messages.append(message)


def make_request() -> LearningRequest:
    return LearningRequest(
        is_learning_request=True,
        skill="Kubernetes operators",
        experience=ExperienceLevel.INTERMEDIATE,
        goal="write my own operator",
        daily_minutes=45,
    )


def make_analysis() -> SkillAnalysis:
    return SkillAnalysis(
        category="Cloud",
        difficulty=ExperienceLevel.ADVANCED,
        estimated_hours=40,
        prerequisites=["Kubernetes basics", "Go"],
        career_paths=["Platform Engineer"],
    )


def test_prompt_carries_every_field_requirement_extracted():
    prompt = build_prompt(make_request())

    assert "Kubernetes operators" in prompt
    assert "intermediate" in prompt
    assert "write my own operator" in prompt
    assert "45" in prompt


def test_prompt_handles_a_missing_goal():
    request = LearningRequest(is_learning_request=True, skill="Rust")

    assert "not stated" in build_prompt(request)


def test_estimated_hours_outside_bounds_is_rejected():
    with pytest.raises(ValidationError):
        SkillAnalysis(category="Cloud", difficulty=ExperienceLevel.BEGINNER, estimated_hours=0)


@pytest.mark.asyncio
async def test_executor_reads_the_request_and_stores_the_analysis(monkeypatch):
    analysis = make_analysis()

    async def fake_analyse(request: LearningRequest) -> SkillAnalysis:
        assert request.skill == "Kubernetes operators"
        return analysis

    monkeypatch.setattr(skill_module, "analyse_skill", fake_analyse)
    state = CourseState(job_id="j1", user_id="u1", prompt="p", request=make_request())
    state.mark(WorkflowStep.REQUIREMENT)
    ctx = CapturingContext()

    await SkillAnalysisExecutor(id=WorkflowStep.SKILL_ANALYSIS).run(state, ctx)

    assert state.skill_analysis is analysis
    assert state.completed_steps == [WorkflowStep.REQUIREMENT, WorkflowStep.SKILL_ANALYSIS]
    assert state.percent == (
        STEP_WEIGHTS[WorkflowStep.REQUIREMENT] + STEP_WEIGHTS[WorkflowStep.SKILL_ANALYSIS]
    )
    assert ctx.messages == [state]


@pytest.mark.parametrize(
    ("request_", "expected"),
    [
        (None, (False, False)),
        (LearningRequest(is_learning_request=True, skill="Rust"), (False, False)),
        (LearningRequest(is_learning_request=False), (True, False)),
        (
            LearningRequest(is_learning_request=True, alternatives=["React", "Vue"]),
            (False, True),
        ),
        (
            LearningRequest(
                is_learning_request=True, missing_requirements=[MissingRequirement.SKILL]
            ),
            (False, True),
        ),
        (LearningRequest(is_learning_request=True), (False, True)),
    ],
    ids=[
        "not-yet-extracted",
        "clear-request",
        "off-topic",
        "unanswered-choice",
        "too-broad",
        "no-skill-at-all",
    ],
)
def test_at_most_one_early_exit_claims_a_request(request_, expected):
    """The default route carries anything neither case claims, so overlap is the only danger.

    An off-topic prompt has no skill either, so the clarify case must stand down for it —
    otherwise small talk would be answered with a question about which skill to learn.
    """
    state = CourseState(job_id="j1", user_id="u1", prompt="p", request=request_)

    assert (_is_not_learning_request(state), _needs_clarification(state)) == expected


def test_naming_one_alternative_is_not_a_choice_to_make():
    """A single entry means the model listed something it had already settled on."""
    state = CourseState(
        job_id="j1",
        user_id="u1",
        prompt="p",
        request=LearningRequest(is_learning_request=True, skill="Vue", alternatives=["Vue"]),
    )

    assert not _needs_clarification(state)
