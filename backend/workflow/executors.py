"""Deterministic (non-agent) executors, such as the course publisher step."""

from __future__ import annotations

from agent_framework import Executor, WorkflowContext, handler

from backend.workflow.state import CourseState, Rejection

REJECTED_ID = "rejected"

REJECTION_MESSAGE = (
    "I couldn't tell what you'd like to learn. Try something like "
    '"Teach me Azure AI Search, 30 minutes a day".'
)


class RejectedExecutor(Executor):
    """Terminal node for prompts that are not learning requests."""

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState, Rejection]) -> None:
        await ctx.yield_output(Rejection(message=REJECTION_MESSAGE))
