"""Tests for the two node-2 exits: asking about a subject, and confirming one."""

from __future__ import annotations

import pytest

from backend.workflow.executors import (
    ConfirmSubjectExecutor,
    SubjectClarifyExecutor,
    ambiguous_message,
)
from backend.workflow.state import (
    Clarification,
    CourseState,
    IdentityStatus,
    LearningRequest,
    SourceDocument,
    SubjectAnalysis,
    SubjectConfirmation,
    TechnicalSubjectType,
)


class RecordingContext:
    def __init__(self) -> None:
        self.outputs: list[object] = []

    async def yield_output(self, value: object) -> None:
        self.outputs.append(value)


def state_for(
    status: IdentityStatus,
    *,
    skill: str = "Agent Framework",
    candidates: list[str] | None = None,
    name: str | None = "Microsoft Agent Framework",
    sources: list[str] | None = None,
) -> CourseState:
    return CourseState(
        job_id="j",
        user_id="u",
        prompt="p",
        request=LearningRequest(is_learning_request=True, skill=skill),
        subject=SubjectAnalysis(
            identity_status=status,
            canonical_name=name,
            subject_type=TechnicalSubjectType.SOFTWARE_FRAMEWORK,
            description="A framework for AI agents.",
            candidates=candidates or [],
        ),
        sources=[
            SourceDocument(title="t", url=url, text="x") for url in (sources or [])
        ],
    )


@pytest.mark.asyncio
async def test_an_ambiguous_subject_hands_back_the_candidates_as_data():
    """A card needs the options themselves; re-parsing them out of prose is a second bug."""
    ctx = RecordingContext()
    state = state_for(IdentityStatus.AMBIGUOUS, candidates=["Microsoft Agent Framework", "OpenAI Agents SDK"])

    await SubjectClarifyExecutor(id="subject-clarify").run(state, ctx)

    assert isinstance(ctx.outputs[0], Clarification)
    assert ctx.outputs[0].options == ["Microsoft Agent Framework", "OpenAI Agents SDK"]


@pytest.mark.asyncio
async def test_an_unrecognised_subject_asks_for_the_exact_name_and_offers_nothing():
    """There is nothing to choose between, so listing options would mean inventing them."""
    ctx = RecordingContext()

    await SubjectClarifyExecutor(id="subject-clarify").run(
        state_for(IdentityStatus.UNRECOGNISED, skill="Blorptagon SDK"), ctx
    )

    assert ctx.outputs[0].options == []
    assert "Blorptagon SDK" in ctx.outputs[0].message


@pytest.mark.asyncio
async def test_a_subject_we_could_not_read_is_not_told_it_does_not_exist():
    """"We could not read enough" and "nothing describes this" are different facts, and
    telling a learner their subject is not real because our fetch was blocked is the same
    class of mistake this node exists to prevent."""
    ctx = RecordingContext()

    await SubjectClarifyExecutor(id="subject-clarify").run(
        state_for(IdentityStatus.INSUFFICIENT_EVIDENCE, skill="Apache Iceberg"), ctx
    )

    message = ctx.outputs[0].message
    assert "Apache Iceberg" in message
    assert "couldn't read enough" in message
    assert ctx.outputs[0].options == []


@pytest.mark.asyncio
async def test_nothing_is_generated_before_the_subject_is_settled():
    """The whole point of stopping here is that no chapter has been paid for yet."""
    state = state_for(IdentityStatus.UNRECOGNISED)

    await SubjectClarifyExecutor(id="subject-clarify").run(state, RecordingContext())

    assert state.curriculum is None and state.chapters == []


def test_the_question_names_every_candidate():
    message = ambiguous_message("Agent Framework", ["A", "B"])

    assert "Agent Framework" in message and "A, B" in message


@pytest.mark.asyncio
async def test_the_confirmation_shows_the_pages_the_course_would_be_built_from():
    """Ranking skew is invisible to every automated check, so the learner sees the evidence."""
    ctx = RecordingContext()
    state = state_for(
        IdentityStatus.CONFIRMED,
        sources=["https://learn.microsoft.com/agent-framework/", "https://github.com/microsoft/agent-framework"],
    )

    await ConfirmSubjectExecutor(id="confirm-subject").run(state, ctx)

    confirmation = ctx.outputs[0]
    assert isinstance(confirmation, SubjectConfirmation)
    assert confirmation.canonical_name == "Microsoft Agent Framework"
    assert confirmation.description == "A framework for AI agents."
    assert confirmation.source_urls == [
        "https://learn.microsoft.com/agent-framework/",
        "https://github.com/microsoft/agent-framework",
    ]


@pytest.mark.asyncio
async def test_the_confirmation_falls_back_to_what_the_learner_asked_for():
    """canonical_name may be null when no document stated one; the card still needs a name."""
    ctx = RecordingContext()

    await ConfirmSubjectExecutor(id="confirm-subject").run(
        state_for(IdentityStatus.CONFIRMED, skill="Rust", name=None), ctx
    )

    assert ctx.outputs[0].canonical_name == "Rust"
