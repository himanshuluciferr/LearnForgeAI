"""chapter-agent — writes the prose for every chapter in the curriculum."""

from __future__ import annotations

import logging
from functools import lru_cache

from agent_framework import Agent, Executor, WorkflowContext, handler

from backend.agents.fanout import per_chapter
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
    ReviewResult,
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

# Every source goes into every chapter prompt, so this multiplies by the number of chapters.
CHARS_PER_SOURCE = 4_000


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
    """The retrieved text itself, not a description of it.

    This used to hand over a title, a URL and a summary the research model had written about a
    page it never opened, which meant every chapter was written from model memory with a
    citation attached.
    """
    if not sources:
        return "None."
    return "\n\n".join(
        f"[{number}] {source.title} ({source.url})\n{source.text[:CHARS_PER_SOURCE]}"
        for number, source in enumerate(sources, start=1)
    )


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


def format_issues(issues: list[str]) -> str:
    """Without this the rewrite is a fresh sample of the same prompt and comes back as weak."""
    if not issues:
        return ""
    listed = "\n".join(f"- {issue}" for issue in issues)
    return (
        "\n\nA reviewer rejected your previous draft of this chapter. Fix every one of "
        f"these in the rewrite:\n{listed}"
    )


def build_prompt(
    request: LearningRequest,
    curriculum: Curriculum,
    outline: ChapterOutline,
    sources: list[ResearchSource],
    issues: list[str] | None = None,
) -> str:
    objectives = "\n".join(f"- {objective}" for objective in outline.objectives) or "- not stated"
    return (
        f"Course: {curriculum.title}\n"
        f"Skill: {request.skill}\n"
        f"Learner's level: {request.assumed_level}\n"
        f"Goal: {request.goal or 'not stated'}\n"
        f"Course language: {request.language}\n"
        f"Target length: about {target_words(request.minutes_per_day)} words.\n\n"
        f"Write chapter {outline.number} of {len(curriculum.chapters)}: {outline.title}\n\n"
        f"By the end the learner must be able to:\n{objectives}\n\n"
        f"{covered_so_far(curriculum, outline)}\n\n"
        f"{coming_later(curriculum, outline)}\n\n"
        f"Sources you may draw on:\n{format_sources(sources)}"
        f"{format_issues(issues or [])}"
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
    issues: list[str] | None = None,
) -> Chapter:
    response = await get_chapter_agent().run(
        build_prompt(request, curriculum, outline, sources, issues)
    )
    draft: ChapterDraft = response.value

    if not any(section.markdown.strip() for section in draft.sections):
        raise ValueError(f"chapter-agent returned an empty body for chapter {outline.number}")

    return assemble(outline, draft)


async def write_chapters(
    request: LearningRequest, curriculum: Curriculum, sources: list[ResearchSource]
) -> list[Chapter]:
    async def write_one(outline: ChapterOutline) -> Chapter:
        return await write_chapter(request, curriculum, outline, sources)

    return await per_chapter(
        AGENT_NAME, curriculum.chapters, write_one, MAX_CONCURRENT_CHAPTERS
    )


async def rewrite_chapters(
    request: LearningRequest,
    curriculum: Curriculum,
    sources: list[ResearchSource],
    review: ReviewResult,
) -> list[Chapter]:
    """Rewrite only the chapters the review flagged, each told what was wrong with it."""
    targets = set(review.regenerate_chapters)
    outlines = [outline for outline in curriculum.chapters if outline.number in targets]

    async def write_one(outline: ChapterOutline) -> Chapter:
        return await write_chapter(
            request, curriculum, outline, sources, review.chapter_issues.get(outline.number, [])
        )

    return await per_chapter(AGENT_NAME, outlines, write_one, MAX_CONCURRENT_CHAPTERS)


def splice(existing: list[Chapter], rewritten: list[Chapter]) -> list[Chapter]:
    """Drop rewrites back into place, leaving the chapters that passed untouched."""
    replaced = {chapter.number: chapter for chapter in rewritten}
    return [replaced.get(chapter.number, chapter) for chapter in existing]


class ChapterExecutor(Executor):
    """Graph node for chapter-agent."""

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState]) -> None:
        assert state.request is not None and state.curriculum is not None

        if state.review is not None and state.review.regenerate_chapters:
            # Counted here, not in review, so the cap counts rewrites actually performed.
            state.revision_count += 1
            rewritten = await rewrite_chapters(
                state.request, state.curriculum, state.research, state.review
            )
            state.chapters = splice(state.chapters, rewritten)
        else:
            state.chapters = await write_chapters(
                state.request, state.curriculum, state.research
            )

        state.mark(WorkflowStep.CHAPTER)
        await ctx.send_message(state)

