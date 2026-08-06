"""Deterministic (non-agent) executors, such as the course publisher step."""

from __future__ import annotations

from agent_framework import Executor, WorkflowContext, handler

from backend.services.artifact_store import artifact_store
from backend.skills.exporter.skill import render_course
from backend.workflow.state import (
    Clarification,
    CourseState,
    PublishedCourse,
    Rejection,
    WorkflowStep,
)

REJECTED_ID = "rejected"
CLARIFY_ID = "clarify"

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


class ClarifyExecutor(Executor):
    """Terminal node for a learner who named several skills and chose none.

    It stops before the expensive half rather than picking for them, so the cost of asking
    is one model call instead of a whole course on the wrong subject.
    """

    @handler
    async def run(
        self, state: CourseState, ctx: WorkflowContext[CourseState, Clarification]
    ) -> None:
        assert state.request is not None
        options = state.request.alternatives
        await ctx.yield_output(Clarification(message=choice_message(options), options=options))


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
