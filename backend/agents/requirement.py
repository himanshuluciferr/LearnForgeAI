"""requirement-agent — extracts skill, experience, goal, daily time, language."""

from __future__ import annotations

from functools import lru_cache

from agent_framework import Agent, Executor, WorkflowContext, handler

from backend.prompts.loader import load_prompt
from backend.services.foundry import get_chat_client
from backend.workflow.state import CourseState, LearningRequest, WorkflowStep

# Foundry agent names allow alphanumerics and interior hyphens only.
AGENT_NAME = "requirement-agent"


@lru_cache
def get_requirement_agent() -> Agent:
    return get_chat_client().as_agent(
        name=AGENT_NAME,
        instructions=load_prompt("requirement"),
        default_options={"response_format": LearningRequest},
    )


async def extract_requirement(prompt: str) -> LearningRequest:
    response = await get_requirement_agent().run(prompt)
    return response.value


class RequirementExecutor(Executor):
    """Graph node for requirement-agent."""

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState]) -> None:
        state.request = await extract_requirement(state.prompt)
        state.mark(WorkflowStep.REQUIREMENT)
        await ctx.send_message(state)
