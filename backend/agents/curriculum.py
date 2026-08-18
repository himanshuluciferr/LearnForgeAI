"""curriculum-agent — produces the ordered course outline."""

from __future__ import annotations

import logging
from functools import lru_cache

from agent_framework import Agent, Executor, WorkflowContext, handler

from backend.agents.chapter import CHARS_PER_TOPIC
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

# One writer call per topic, so the topic budget is what actually sets the cost of a course.
MIN_TOPICS_PER_CHAPTER = 3
MAX_TOPICS_PER_CHAPTER = 8

# Not a new constant: a chapter is worth planning only if the evidence can fund its minimum
# topics, and the writer's own per-topic budget is what says how much text that takes.
CHARS_PER_CHAPTER = CHARS_PER_TOPIC * MIN_TOPICS_PER_CHAPTER


@lru_cache
def get_curriculum_agent() -> Agent:
    return get_chat_client().as_agent(
        name=AGENT_NAME,
        instructions=load_prompt("curriculum"),
        default_options={"response_format": Curriculum},
    )


def plan_chapter_count(subject: SubjectAnalysis, sources: list[ResearchSource]) -> int:
    """One chapter per area the documents cover, but never more chapters than there is text.

    This used to divide an `estimated_hours` the model supplied, which was measured swinging
    40/120/40 on one subject across three runs — a 3x difference in course length from noise.
    Counting the areas that were actually found is at least grounded in what was read.
    ⚠️ `len(scope)` is still unstable: 6/4/5 on three identical git runs.

    The evidence cap comes from a measured failure. A Microsoft Agent Framework run planned 11
    chapters over 75,073 chars, so every chapter shared the same thin material and the writer
    invented an API — every symbol present in the sources came out right, every symbol absent
    came out wrong. `CHARS_PER_CHAPTER` is the writer's own budget, so this is simply how many
    chapters' worth of distinct text we actually hold.
    """
    afforded = sum(len(source.text) for source in sources) // CHARS_PER_CHAPTER
    if afforded < len(subject.scope):
        logger.info(
            "curriculum-agent: evidence supports %d chapters, scope named %d",
            afforded,
            len(subject.scope),
        )
    return max(MIN_CHAPTERS, min(MAX_CHAPTERS, len(subject.scope), afforded))


def plan_topic_count(chapters: int, sources: list[ResearchSource]) -> int:
    """How many topics each chapter may hold, from the text we actually retrieved.

    Depth has to follow the evidence rather than the learner's clock. `target_words` is one
    number for every chapter regardless of subject, which is why a large area and a small one
    came out the same length; the topic count is the lever that lets them differ.
    """
    afforded = sum(len(source.text) for source in sources) // CHARS_PER_TOPIC
    return max(MIN_TOPICS_PER_CHAPTER, min(MAX_TOPICS_PER_CHAPTER, afforded // chapters))


def format_sources(sources: list[ResearchSource]) -> str:
    """Titles only. Planning needs to know what ground the sources cover; the text itself goes
    to the chapter writer, where it is actually read."""
    if not sources:
        return "None."
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
    chapters = plan_chapter_count(subject, sources)
    topics = plan_topic_count(chapters, sources)
    prerequisites = ", ".join(subject.prerequisites) or "none"
    return (
        f"Skill: {subject.canonical_name or request.skill}\n"
        f"What it is: {subject.description}\n"
        f"Areas it covers: {', '.join(subject.scope) or 'not established'}\n"
        f"Learner's current level: {request.assumed_level}\n"
        f"Goal: {request.goal or 'not stated'}\n"
        f"Assumed knowledge, do not teach: {prerequisites}\n"
        f"Where to start: {starting_point(request)}\n"
        f"Course length: {chapters} chapters\n"
        f"Course language: {request.language}\n"
        f"Produce exactly {chapters} chapters, each holding at most {topics} topics.\n"
        f"Give a chapter fewer topics where the sources are thin on its area; the limit is a "
        f"ceiling, not a quota.\n\n"
        f"Verified sources:\n{format_sources(sources)}"
    )


def tidy(curriculum: Curriculum, topics_per_chapter: int) -> Curriculum:
    """Enforces the count caps and renumbers, so chapter numbers are ours rather than the model's."""
    if len(curriculum.chapters) > MAX_CHAPTERS:
        logger.info(
            "curriculum-agent: trimming %d chapters to %d",
            len(curriculum.chapters),
            MAX_CHAPTERS,
        )
        curriculum.chapters = curriculum.chapters[:MAX_CHAPTERS]

    for position, chapter in enumerate(curriculum.chapters, start=1):
        chapter.number = position
        if len(chapter.topics) > topics_per_chapter:
            logger.info(
                "curriculum-agent: trimming chapter %d from %d topics to %d",
                position,
                len(chapter.topics),
                topics_per_chapter,
            )
            chapter.topics = chapter.topics[:topics_per_chapter]
    return curriculum


async def plan_curriculum(
    request: LearningRequest, subject: SubjectAnalysis, sources: list[ResearchSource]
) -> Curriculum:
    response = await get_curriculum_agent().run(build_prompt(request, subject, sources))
    curriculum: Curriculum = response.value

    # Unlike missing research, a course with no chapters is not a degraded result but a broken one.
    if not curriculum.chapters:
        raise ValueError("curriculum-agent returned no chapters")

    chapters = plan_chapter_count(subject, sources)
    return tidy(curriculum, plan_topic_count(chapters, sources))


class CurriculumExecutor(Executor):
    """Graph node for curriculum-agent."""

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState]) -> None:
        assert state.request is not None and state.subject is not None
        state.curriculum = await plan_curriculum(state.request, state.subject, state.research)
        state.mark(WorkflowStep.CURRICULUM)
        await ctx.send_message(state)
