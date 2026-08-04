"""review-agent — the quality gate that can send weak chapters back to be rewritten."""

from __future__ import annotations

import logging
from functools import lru_cache

from agent_framework import Agent, Executor, WorkflowContext, handler

from backend.agents.fanout import per_chapter
from backend.prompts.loader import load_prompt
from backend.services.foundry import get_chat_client
from backend.workflow.state import (
    PASSING_REVIEW_SCORE,
    Chapter,
    ChapterVerdict,
    CourseState,
    CourseVerdict,
    Curriculum,
    LearningRequest,
    ReviewResult,
    WorkflowStep,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "review-agent"
CHAPTER_AGENT_NAME = "review-chapter-agent"
COURSE_AGENT_NAME = "review-course-agent"

MAX_CONCURRENT_REVIEWS = 4


@lru_cache
def get_chapter_review_agent() -> Agent:
    return get_chat_client().as_agent(
        name=CHAPTER_AGENT_NAME,
        instructions=load_prompt("review_chapter"),
        default_options={"response_format": ChapterVerdict},
    )


@lru_cache
def get_course_review_agent() -> Agent:
    return get_chat_client().as_agent(
        name=COURSE_AGENT_NAME,
        instructions=load_prompt("review_course"),
        default_options={"response_format": CourseVerdict},
    )


def clamp_score(score: int) -> int:
    """A score outside the range would silently distort both the average and the rewrite list."""
    return max(0, min(100, score))


def build_chapter_prompt(request: LearningRequest, chapter: Chapter) -> str:
    takeaways = "\n".join(f"- {point}" for point in chapter.key_points) or "- none stated"
    return (
        f"Skill: {request.skill}\n"
        f"Learner's level: {request.experience}\n"
        f"Learner's goal: {request.goal or 'not stated'}\n\n"
        f"Chapter {chapter.number}: {chapter.title}\n\n"
        f"The chapter claims these takeaways:\n{takeaways}\n\n"
        f"---\n{chapter.body_markdown}\n---"
    )


def format_outline(curriculum: Curriculum, chapters: list[Chapter]) -> str:
    """Titles and takeaways only. Full prose would not fit, and is judged per chapter anyway."""
    landed = {chapter.number: chapter for chapter in chapters}
    lines = []
    for outline in curriculum.chapters:
        chapter = landed.get(outline.number)
        teaches = "; ".join(chapter.key_points) if chapter else "not written"
        lines.append(f"- Ch {outline.number} {outline.title}\n    teaches: {teaches}")
    return "\n".join(lines)


def build_course_prompt(
    request: LearningRequest, curriculum: Curriculum, chapters: list[Chapter]
) -> str:
    return (
        f"Skill: {request.skill}\n"
        f"Learner's level: {request.experience}\n"
        f"Learner's goal: {request.goal or 'not stated'}\n\n"
        f"Course: {curriculum.title}\n"
        f"{curriculum.summary}\n\n"
        f"The chapters, in order:\n{format_outline(curriculum, chapters)}"
    )


async def review_chapter(request: LearningRequest, chapter: Chapter) -> ChapterVerdict:
    response = await get_chapter_review_agent().run(build_chapter_prompt(request, chapter))
    verdict: ChapterVerdict = response.value
    return ChapterVerdict(score=clamp_score(verdict.score), issues=verdict.issues)


async def review_whole_course(
    request: LearningRequest, curriculum: Curriculum, chapters: list[Chapter]
) -> CourseVerdict:
    response = await get_course_review_agent().run(
        build_course_prompt(request, curriculum, chapters)
    )
    return response.value


def overall_score(verdicts: list[ChapterVerdict]) -> int:
    """The mean of the chapters. Asking for a course score as well would hand us a second
    number free to disagree with the first."""
    if not verdicts:
        return 0
    return round(sum(verdict.score for verdict in verdicts) / len(verdicts))


def collect_issues(
    course: CourseVerdict, numbered: list[tuple[int, ChapterVerdict]]
) -> list[str]:
    """Course-level faults first, then per-chapter ones tagged with where they were found."""
    issues = list(course.issues)
    for number, verdict in numbered:
        issues.extend(f"Chapter {number}: {issue}" for issue in verdict.issues)
    return issues


def build_result(
    course: CourseVerdict, numbered: list[tuple[int, ChapterVerdict]]
) -> ReviewResult:
    verdicts = [verdict for _, verdict in numbered]
    weak = [number for number, verdict in numbered if verdict.score < PASSING_REVIEW_SCORE]
    return ReviewResult(
        score=overall_score(verdicts),
        issues=collect_issues(course, numbered),
        regenerate_chapters=weak,
        chapter_issues={
            number: verdict.issues for number, verdict in numbered if verdict.issues
        },
    )


async def review_course(
    request: LearningRequest, curriculum: Curriculum, chapters: list[Chapter]
) -> ReviewResult:
    """One focused call per chapter, plus one over the outline.

    A single call holding every chapter is the giant-prompt failure this architecture exists
    to avoid: the model skims and returns feedback that would fit any course.
    """
    if not chapters:
        raise ValueError("review-agent was given no chapters to review")

    async def review_one(chapter: Chapter) -> ChapterVerdict:
        return await review_chapter(request, chapter)

    verdicts = await per_chapter(AGENT_NAME, chapters, review_one, MAX_CONCURRENT_REVIEWS)
    # per_chapter returns input order, so chapters and verdicts still line up.
    numbered = [(chapter.number, verdict) for chapter, verdict in zip(chapters, verdicts)]

    course = await review_whole_course(request, curriculum, chapters)
    result = build_result(course, numbered)
    logger.info(
        "review-agent: scored %d, rewriting %s",
        result.score,
        result.regenerate_chapters or "nothing",
    )
    return result


class ReviewExecutor(Executor):
    """Graph node for review-agent."""

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState]) -> None:
        assert state.request is not None and state.curriculum is not None
        state.review = await review_course(state.request, state.curriculum, state.chapters)
        state.mark(WorkflowStep.REVIEW)
        await ctx.send_message(state)
