"""research-agent — gathers trusted sources using the research and ranking tools."""

from __future__ import annotations

import logging
from functools import lru_cache

from agent_framework import Agent, Executor, WorkflowContext, handler

from backend.prompts.loader import load_prompt
from backend.services.foundry import get_chat_client
from backend.services.web_search import SearchHit, search_web
from backend.skills.ranking.skill import rank_sources
from backend.skills.research.skill import verify_sources
from backend.workflow.state import (
    CourseState,
    LearningRequest,
    ResearchSource,
    SkillAnalysis,
    SourceSelection,
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
        default_options={"response_format": SourceSelection},
    )


def format_hits(hits: list[SearchHit]) -> str:
    return "\n".join(
        f"{index}. [{hit.kind}] {hit.title}\n   {hit.url}\n   {hit.snippet}"
        for index, hit in enumerate(hits)
    )


def build_prompt(request: LearningRequest, analysis: SkillAnalysis, hits: list[SearchHit]) -> str:
    prerequisites = ", ".join(analysis.prerequisites) or "none"
    return (
        f"Skill: {request.skill}\n"
        f"Skill difficulty: {analysis.difficulty}\n"
        f"Learner's current level: {request.experience}\n"
        f"Goal: {request.goal or 'not stated'}\n"
        f"Assumed prerequisites: {prerequisites}\n"
        f"Course size: about {analysis.estimated_hours} hours\n\n"
        f"Pages found by searching for {request.skill!r}:\n{format_hits(hits)}"
    )


def collect(hits: list[SearchHit], selection: SourceSelection) -> list[ResearchSource]:
    """Rebuilds sources from the hits themselves, so a mistyped URL cannot reach the course."""
    chosen: list[ResearchSource] = []
    seen: set[int] = set()
    for pick in selection.picks:
        if not 0 <= pick.index < len(hits) or pick.index in seen:
            logger.info("research-agent: ignoring pick %d", pick.index)
            continue
        seen.add(pick.index)
        hit = hits[pick.index]
        chosen.append(
            ResearchSource(title=hit.title, url=hit.url, kind=pick.kind, summary=pick.summary)
        )
    return chosen[:MAX_SOURCES]


async def gather_sources(
    request: LearningRequest, analysis: SkillAnalysis
) -> list[ResearchSource]:
    """Search, then choose, then verify, then rank. The model only does the choosing.

    The query is the skill the learner named, never `analysis.category`, which is the field
    the model quietly rewrites when it has not heard of the skill.
    """
    hits = await search_web(request.skill)
    if not hits:
        return []

    response = await get_research_agent().run(build_prompt(request, analysis, hits))
    proposed = collect(hits, response.value)

    verified = await verify_sources(proposed, request.skill)
    on_topic = sum(source.mentions_skill for source in verified)
    logger.info(
        "research-agent: %d found, %d chosen, %d reachable, %d naming the skill",
        len(hits),
        len(proposed),
        len(verified),
        on_topic,
    )
    return rank_sources(verified)


def confirm_on_topic(skill: str, sources: list[ResearchSource]) -> None:
    """Stops a run that has nothing to stand on.

    A model that has never heard of a skill does not say so: the response schema has no
    "I don't know" branch, so it answers about the nearest thing it does know. Sources that
    never name the skill are the cheapest evidence we have that this has happened.
    """
    if not sources:
        raise ValueError(
            f"No sources could be verified for {skill!r}, so the course would be written "
            "from the model's own memory alone. Refusing to generate one."
        )

    if not any(source.mentions_skill for source in sources):
        found = ", ".join(source.title for source in sources[:3])
        raise ValueError(
            f"None of the {len(sources)} verified sources mention {skill!r}. They are about "
            f"something else ({found}). Refusing to generate a course on the wrong subject."
        )


class ResearchExecutor(Executor):
    """Graph node for research-agent."""

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState]) -> None:
        assert state.request is not None and state.skill_analysis is not None
        state.research = await gather_sources(state.request, state.skill_analysis)
        confirm_on_topic(state.request.skill, state.research)
        state.mark(WorkflowStep.RESEARCH)
        await ctx.send_message(state)
