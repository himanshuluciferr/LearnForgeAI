"""research-agent — gathers trusted sources using the research and ranking tools."""

from __future__ import annotations

import logging
from functools import lru_cache

from agent_framework import Agent, Executor, WorkflowContext, handler

from backend.prompts.loader import load_prompt
from backend.services.foundry import get_chat_client
from backend.skills.ranking.skill import rank_sources
from backend.skills.research.skill import verify_sources
from backend.workflow.state import (
    CourseState,
    LearningRequest,
    ResearchBundle,
    ResearchSource,
    SubjectAnalysis,
    WorkflowStep,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "research-agent"

# Caps how many links we fetch per job, since each one is an outbound request.
MAX_SOURCES = 8


@lru_cache
def get_research_agent() -> Agent:
    return get_chat_client().as_agent(
        name=AGENT_NAME,
        instructions=load_prompt("research"),
        default_options={"response_format": ResearchBundle},
    )


def build_prompt(request: LearningRequest, subject: SubjectAnalysis) -> str:
    prerequisites = ", ".join(subject.prerequisites) or "none"
    return (
        f"Skill: {subject.canonical_name or request.skill}\n"
        f"What it is: {subject.description}\n"
        f"Areas it covers: {', '.join(subject.scope) or 'not established'}\n"
        f"Learner's current level: {request.assumed_level}\n"
        f"Goal: {request.goal or 'not stated'}\n"
        f"Assumed prerequisites: {prerequisites}"
    )


async def gather_sources(
    request: LearningRequest, subject: SubjectAnalysis
) -> list[ResearchSource]:
    """Propose, then verify, then rank. Only the proposing step involves the model."""
    response = await get_research_agent().run(build_prompt(request, subject))
    proposed = response.value.sources[:MAX_SOURCES]

    verified = await verify_sources(proposed)
    if len(verified) < len(proposed):
        logger.info(
            "research-agent: kept %d of %d proposed sources", len(verified), len(proposed)
        )
    return rank_sources(verified)


class ResearchExecutor(Executor):
    """Graph node for research-agent."""

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState]) -> None:
        assert state.request is not None and state.subject is not None
        # An empty list is a valid outcome: an ungrounded course still beats a failed job.
        state.research = await gather_sources(state.request, state.subject)
        state.mark(WorkflowStep.RESEARCH)
        await ctx.send_message(state)
