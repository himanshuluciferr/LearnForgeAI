"""chapter-agent — writes the prose for every chapter in the curriculum."""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

from agent_framework import Agent, Executor, WorkflowContext, handler

from backend.prompts.loader import load_prompt
from backend.services.foundry import get_chat_client
from backend.workflow.state import (
    Chapter,
    ChapterDraft,
    ChapterOutline,
    ChapterSection,
    CourseState,
    Curriculum,
    LearningRequest,
    ResearchSource,
    WorkflowStep,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "chapter-agent"

# The first step whose cost scales with the plan: one model call per chapter. High enough to
# keep a twenty-chapter course tolerable, low enough to stay under the deployment's rate limit.
MAX_CONCURRENT_CHAPTERS = 4

# A chapter should fit roughly one sitting, so length follows the learner's daily minutes.
WORDS_PER_SESSION_MINUTE = 25
MIN_WORDS = 600
MAX_WORDS = 2000


@lru_cache
def get_chapter_agent() -> Agent:
    return get_chat_client().as_agent(
        name=AGENT_NAME,
        instructions=load_prompt("chapter"),
        default_options={"response_format": ChapterDraft},
    )


def target_words(daily_minutes: int) -> int:
    """Length is arithmetic on the learner's schedule, so we work it out rather than ask."""
    return max(MIN_WORDS, min(MAX_WORDS, daily_minutes * WORDS_PER_SESSION_MINUTE))


def format_sources(sources: list[ResearchSource]) -> str:
    """Summaries are included here, unlike in planning, so the writer can tell which source
    actually covers this chapter's topic."""
    if not sources:
        return (
            "None were verified. Write from general knowledge, and avoid specific version "
            "numbers, pricing and quota figures you cannot check."
        )
    return "\n".join(f"- {source.title} ({source.url})\n  {source.summary}" for source in sources)


def covered_so_far(curriculum: Curriculum, outline: ChapterOutline) -> str:
    """Each chapter is written by its own call with no memory of the others, so the only
    thing stopping chapter 5 re-teaching chapter 2 is being told what chapter 2 did."""
    earlier = [item for item in curriculum.chapters if item.number < outline.number]
    if not earlier:
        return (
            "This is the first chapter. Nothing has been taught yet, so introduce every term "
            "you use."
        )
    covered = "\n".join(
        f"- Ch {item.number} {item.title}: {'; '.join(item.objectives)}" for item in earlier
    )
    return (
        "Already taught. Assume the learner knows this and do not explain it again — refer "
        f"back by chapter number instead:\n{covered}"
    )


def coming_later(curriculum: Curriculum, outline: ChapterOutline) -> str:
    later = [item for item in curriculum.chapters if item.number > outline.number]
    if not later:
        return (
            "This is the final chapter. Close the course off rather than pointing forward to "
            "material that does not exist."
        )
    titles = "\n".join(f"- Ch {item.number} {item.title}" for item in later)
    return f"Reserved for later chapters. Mention in passing at most, never teach:\n{titles}"


def build_prompt(
    request: LearningRequest,
    curriculum: Curriculum,
    outline: ChapterOutline,
    sources: list[ResearchSource],
) -> str:
    objectives = "\n".join(f"- {objective}" for objective in outline.objectives) or "- not stated"
    return (
        f"Course: {curriculum.title}\n"
        f"Skill: {request.skill}\n"
        f"Learner's level: {request.experience}\n"
        f"Goal: {request.goal or 'not stated'}\n"
        f"Course language: {request.language}\n"
        f"Target length: about {target_words(request.daily_minutes)} words.\n\n"
        f"Write chapter {outline.number} of {len(curriculum.chapters)}: {outline.title}\n\n"
        f"By the end the learner must be able to:\n{objectives}\n\n"
        f"{covered_so_far(curriculum, outline)}\n\n"
        f"{coming_later(curriculum, outline)}\n\n"
        f"Sources you may draw on:\n{format_sources(sources)}"
    )


def render_body(sections: list[ChapterSection]) -> str:
    """Asked for one Markdown blob the model returned prose with no headings at all, so it
    is asked for titled sections instead and the Markdown structure is produced here."""
    return "\n\n".join(
        f"## {section.heading}\n\n{section.markdown.strip()}" for section in sections
    )


def assemble(outline: ChapterOutline, draft: ChapterDraft) -> Chapter:
    """Number and title come from the plan, not the draft, so a chapter cannot drift away
    from the curriculum it was commissioned from."""
    return Chapter(
        number=outline.number,
        title=outline.title,
        body_markdown=render_body(draft.sections),
        key_points=draft.key_points,
        exercises=draft.exercises,
    )


async def write_chapter(
    request: LearningRequest,
    curriculum: Curriculum,
    outline: ChapterOutline,
    sources: list[ResearchSource],
) -> Chapter:
    response = await get_chapter_agent().run(build_prompt(request, curriculum, outline, sources))
    draft: ChapterDraft = response.value

    if not any(section.markdown.strip() for section in draft.sections):
        raise ValueError(f"chapter-agent returned an empty body for chapter {outline.number}")

    return assemble(outline, draft)


async def write_chapters(
    request: LearningRequest, curriculum: Curriculum, sources: list[ResearchSource]
) -> list[Chapter]:
    """Chapters are written concurrently. That is safe because each task returns its own
    Chapter and nothing shared is mutated — unlike a workflow fan-out, which would have
    several executors writing the one CourseState.
    """
    limit = asyncio.Semaphore(MAX_CONCURRENT_CHAPTERS)

    async def write_one(outline: ChapterOutline) -> Chapter:
        async with limit:
            return await write_chapter(request, curriculum, outline, sources)

    logger.info("chapter-agent: writing %d chapters", len(curriculum.chapters))
    results = await asyncio.gather(
        *(write_one(outline) for outline in curriculum.chapters), return_exceptions=True
    )

    chapters: list[Chapter] = []
    failed: list[int] = []
    for outline, result in zip(curriculum.chapters, results):
        if isinstance(result, BaseException):
            logger.error("chapter-agent: chapter %d failed: %s", outline.number, result)
            failed.append(outline.number)
        else:
            chapters.append(result)

    # A course with a hole in it still reads as finished, so a partial result is refused.
    if failed:
        raise ValueError(f"chapter-agent failed on chapters {failed}")

    return chapters


class ChapterExecutor(Executor):
    """Graph node for chapter-agent."""

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState]) -> None:
        assert state.request is not None and state.curriculum is not None
        state.chapters = await write_chapters(state.request, state.curriculum, state.research)
        state.mark(WorkflowStep.CHAPTER)
        await ctx.send_message(state)

