"""curriculum-agent — produces the ordered course outline."""

from __future__ import annotations

import logging
from functools import lru_cache

from agent_framework import Agent, Executor, WorkflowContext, handler

from backend.prompts.loader import load_prompt
from backend.services.foundry import get_chat_client
from backend.workflow.state import (
    CourseState,
    Curriculum,
    ExperienceLevel,
    LearningRequest,
    ResearchSource,
    SubjectAnalysis,
    WorkflowStep,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "curriculum-agent"

# chapter-agent writes prose for every chapter, so this cap bounds the most expensive step.
MAX_CHAPTERS = 20
MIN_CHAPTERS = 5


@lru_cache
def get_curriculum_agent() -> Agent:
    return get_chat_client().as_agent(
        name=AGENT_NAME,
        instructions=load_prompt("curriculum"),
        default_options={"response_format": Curriculum},
    )


def plan_chapter_count(subject: SubjectAnalysis) -> int:
    """One chapter per area the documents say the subject covers.

    This used to divide an `estimated_hours` the model supplied, which was measured swinging
    40/120/40 on one subject across three runs — a 3x difference in course length from noise.
    Counting the areas that were actually found is at least grounded in what was read.
    ⚠️ The stability of `len(scope)` across runs is not yet measured; the clamp bounds it.
    """
    return max(MIN_CHAPTERS, min(MAX_CHAPTERS, len(subject.scope)))


def format_sources(sources: list[ResearchSource]) -> str:
    if not sources:
        return "None were verified. Plan from general knowledge and keep claims conservative."
    return "\n".join(f"- [{source.kind}] {source.title} — {source.url}" for source in sources)


def starting_point(request: LearningRequest) -> str:
    """A general 'adapt to the level' rule gets ignored, so we decide the level here and
    hand the model one concrete instruction about where chapter 1 begins."""
    if request.assumed_level == ExperienceLevel.BEGINNER:
        return f"Chapter 1 may introduce {request.skill} from scratch."
    return (
        f"The learner already uses {request.skill}. Do not spend a chapter on what it is, "
        f"why to use it, its architecture overview, or first-time setup. Chapter 1 must start "
        f"past all of that."
    )


def build_prompt(
    request: LearningRequest, subject: SubjectAnalysis, sources: list[ResearchSource]
) -> str:
    chapters = plan_chapter_count(subject)
    prerequisites = ", ".join(subject.prerequisites) or "none"
    return (
        f"Skill: {subject.canonical_name or request.skill}\n"
        f"What it is: {subject.description}\n"
        f"Areas it covers: {', '.join(subject.scope) or 'not established'}\n"
        f"Learner's current level: {request.assumed_level}\n"
        f"Goal: {request.goal or 'not stated'}\n"
        f"Assumed knowledge, do not teach: {prerequisites}\n"
        f"Where to start: {starting_point(request)}\n"
        f"Course length: {chapters} chapters, one sitting of about "
        f"{request.minutes_per_day} minutes each\n"
        f"Course language: {request.language}\n"
        f"Produce exactly {chapters} chapters.\n\n"
        f"Verified sources:\n{format_sources(sources)}"
    )


def tidy(curriculum: Curriculum) -> Curriculum:
    """Enforces the count cap and renumbers, so chapter numbers are ours rather than the model's."""
    if len(curriculum.chapters) > MAX_CHAPTERS:
        logger.info(
            "curriculum-agent: trimming %d chapters to %d",
            len(curriculum.chapters),
            MAX_CHAPTERS,
        )
        curriculum.chapters = curriculum.chapters[:MAX_CHAPTERS]

    for position, chapter in enumerate(curriculum.chapters, start=1):
        chapter.number = position
    return curriculum


async def plan_curriculum(
    request: LearningRequest, subject: SubjectAnalysis, sources: list[ResearchSource]
) -> Curriculum:
    response = await get_curriculum_agent().run(build_prompt(request, subject, sources))
    curriculum: Curriculum = response.value

    # Unlike missing research, a course with no chapters is not a degraded result but a broken one.
    if not curriculum.chapters:
        raise ValueError("curriculum-agent returned no chapters")

    return tidy(curriculum)


class CurriculumExecutor(Executor):
    """Graph node for curriculum-agent."""

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState]) -> None:
        assert state.request is not None and state.subject is not None
        state.curriculum = await plan_curriculum(state.request, state.subject, state.research)
        state.mark(WorkflowStep.CURRICULUM)
        await ctx.send_message(state)
