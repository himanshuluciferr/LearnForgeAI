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
    SkillAnalysis,
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


def build_prompt(request: LearningRequest, analysis: SkillAnalysis) -> str:
    prerequisites = ", ".join(analysis.prerequisites) or "none"
    return (
        f"Skill: {request.skill}\n"
        f"Field: {analysis.category}\n"
        f"Skill difficulty: {analysis.difficulty}\n"
        f"Learner's current level: {request.experience}\n"
        f"Goal: {request.goal or 'not stated'}\n"
        f"Assumed prerequisites: {prerequisites}\n"
        f"Course size: about {analysis.estimated_hours} hours"
    )


async def gather_sources(
    request: LearningRequest, analysis: SkillAnalysis
) -> list[ResearchSource]:
    """Propose, then verify, then rank. Only the proposing step involves the model."""
    response = await get_research_agent().run(build_prompt(request, analysis))
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
        assert state.request is not None and state.skill_analysis is not None
        # An empty list is a valid outcome: an ungrounded course still beats a failed job.
        state.research = await gather_sources(state.request, state.skill_analysis)
        state.mark(WorkflowStep.RESEARCH)
        await ctx.send_message(state)
