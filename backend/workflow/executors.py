"""Deterministic (non-agent) executors, such as the course publisher step."""

from __future__ import annotations

from agent_framework import Executor, WorkflowContext, handler

from backend.services.artifact_store import artifact_store
from backend.skills.exporter.skill import render_course
from backend.workflow.state import (
    Clarification,
    CourseState,
    IdentityStatus,
    LearningRequest,
    PublishedCourse,
    Rejection,
    SubjectConfirmation,
    WorkflowStep,
)

REJECTED_ID = "rejected"
CLARIFY_ID = "clarify"
SUBJECT_CLARIFY_ID = "subject-clarify"
CONFIRM_SUBJECT_ID = "confirm-subject"

MARKDOWN_FILENAME = "course.md"

REJECTION_MESSAGE = (
    "I couldn't tell what you'd like to learn. Try something like "
    '"Teach me Azure AI Search, 30 minutes a day".'
)


def choice_message(options: list[str]) -> str:
    return (
        f"You mentioned {', '.join(options[:-1])} and {options[-1]}, so I don't know which "
        "course to build. Ask me again naming just one."
    )


MISSING_SKILL_MESSAGE = (
    "Sure — what specific skill or technology would you like to learn? For example: Azure, "
    "React, Python, Microsoft Agent Framework, or Azure AI Search."
)

UNRECOGNISED_MESSAGE = (
    "I couldn't find authoritative sources describing \"{skill}\" as a technology. Could you "
    "give me the exact name, or a link to its documentation?"
)

# Kept apart from UNRECOGNISED: "we could not read enough" is a different fact from "nothing
# we read describes this", and telling a learner their subject does not exist when our fetch
# was blocked is the same class of mistake this node exists to prevent.
INSUFFICIENT_MESSAGE = (
    "I found results for \"{skill}\" but couldn't read enough of them to be sure what it is. "
    "Try again, or send me a link to its documentation."
)


def ambiguous_message(skill: str, candidates: list[str]) -> str:
    named = ", ".join(candidates)
    return (
        f'"{skill}" matches several different technologies — {named}. '
        "Which one would you like to learn?"
    )


class SubjectClarifyExecutor(Executor):
    """Terminal node for a subject that retrieval could not pin down.

    All three non-confirmed outcomes land here before any chapter is paid for. The message is
    assembled from what the analysis already reported, so no model call is spent phrasing a
    question we can already ask.
    """

    @handler
    async def run(
        self, state: CourseState, ctx: WorkflowContext[CourseState, Clarification]
    ) -> None:
        assert state.request is not None and state.subject is not None
        skill = state.request.skill or "that"
        status = state.subject.identity_status
        if status is IdentityStatus.AMBIGUOUS:
            candidates = state.subject.candidates
            await ctx.yield_output(
                Clarification(message=ambiguous_message(skill, candidates), options=candidates)
            )
            return
        template = (
            INSUFFICIENT_MESSAGE
            if status is IdentityStatus.INSUFFICIENT_EVIDENCE
            else UNRECOGNISED_MESSAGE
        )
        await ctx.yield_output(Clarification(message=template.format(skill=skill), options=[]))


class ConfirmSubjectExecutor(Executor):
    """Terminal node that shows the learner what we are about to build a course on.

    The machine gate closes the hole where a model substitutes a subject it half-remembers. It
    cannot close the one where search ranking substitutes it — a name a single vendor dominates
    returns documents that genuinely all describe one subject. So the learner sees the name and
    the pages it came from, and the run stops here until they say yes.
    """

    @handler
    async def run(
        self, state: CourseState, ctx: WorkflowContext[CourseState, SubjectConfirmation]
    ) -> None:
        assert state.request is not None and state.subject is not None
        subject = state.subject
        name = subject.canonical_name or state.request.skill or "this subject"
        await ctx.yield_output(
            SubjectConfirmation(
                message=f"I'll build a course on {name}. Shall I start?",
                canonical_name=name,
                description=subject.description,
                source_urls=[document.url for document in state.sources],
            )
        )


def build_clarification(request: LearningRequest) -> Clarification:
    """Node 1 already worked out what is missing, so the question is assembled in code.

    A second model call to phrase it would be a second chance to change the subject.
    """
    options = request.alternatives
    if len(options) > 1:
        return Clarification(message=choice_message(options), options=options)
    return Clarification(message=MISSING_SKILL_MESSAGE, options=[])


class ClarifyExecutor(Executor):
    """Terminal node for a learner whose message does not name one skill to build on.

    It stops before the expensive half rather than picking for them, so the cost of asking
    is one model call instead of a whole course on the wrong subject.
    """

    @handler
    async def run(
        self, state: CourseState, ctx: WorkflowContext[CourseState, Clarification]
    ) -> None:
        assert state.request is not None
        await ctx.yield_output(build_clarification(state.request))


class RejectedExecutor(Executor):
    """Terminal node for prompts that are not learning requests."""

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState, Rejection]) -> None:
        await ctx.yield_output(Rejection(message=REJECTION_MESSAGE))


class PublisherExecutor(Executor):
    """Renders the finished course and stores it. No model runs here — every word in the
    document was already written and reviewed upstream, so this step is pure assembly.

    It is the last node, so it yields the state as the workflow's output.
    """

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState, CourseState]) -> None:
        url = await artifact_store.put(
            state.user_id, state.job_id, MARKDOWN_FILENAME, render_course(state)
        )
        # pdf_url and docx_url stay None until those renderers exist, rather than being
        # filled with the markdown link and quietly lying about the format.
        state.published = PublishedCourse(markdown_url=url)
        state.mark(WorkflowStep.PUBLISHER)
        await ctx.yield_output(state)
