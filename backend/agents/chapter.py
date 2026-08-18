"""chapter-agent — writes the prose for every topic in the curriculum."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from agent_framework import Agent, Executor, WorkflowContext, handler

from backend.agents.fanout import per_chapter
from backend.prompts.loader import load_prompt
from backend.services.foundry import get_chat_client
from backend.skills.diagrams.skill import render_diagram
from backend.skills.passages.skill import passages_for
from backend.workflow.state import (
    Chapter,
    ChapterDraft,
    ChapterOutline,
    CourseState,
    Curriculum,
    LearningRequest,
    ResearchSource,
    ReviewResult,
    Topic,
    TopicDraft,
    TopicOutline,
    WorkflowStep,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "chapter-agent"
WRAP_AGENT_NAME = "chapter-wrap-agent"


@dataclass
class _TopicJob:
    """One topic to write. `number` is its CHAPTER's number, so a failure names the chapter
    the reader would lose rather than a position inside it."""

    number: int
    outline: ChapterOutline
    topic: TopicOutline
    position: int

# The first step whose cost scales with the plan: one model call per topic. High enough to
# keep a long course tolerable, low enough to stay under the deployment's rate limit.
MAX_CONCURRENT_CHAPTERS = 4

# A topic is one self-contained subject, not a sitting's worth of reading, so its length
# follows what there is to say about it rather than the learner's clock.
MIN_TOPIC_WORDS = 250
MAX_TOPIC_WORDS = 700

# Every source rides in every topic's prompt, so this multiplies by the number of topics.
CHARS_PER_TOPIC = 8_000


@lru_cache
def get_chapter_agent() -> Agent:
    return get_chat_client().as_agent(
        name=AGENT_NAME,
        instructions=load_prompt("chapter"),
        default_options={"response_format": TopicDraft},
    )


@lru_cache
def get_wrap_agent() -> Agent:
    """A second agent because the wrap call needs a different response_format, not a
    different job — same instructions, judged over the finished chapter."""
    return get_chat_client().as_agent(
        name=WRAP_AGENT_NAME,
        instructions=load_prompt("chapter_wrap"),
        default_options={"response_format": ChapterDraft},
    )


def target_words(objectives: list[str]) -> int:
    """Length follows how much the topic promised to cover, so a large subject and a small
    one no longer come out the same size."""
    return max(MIN_TOPIC_WORDS, min(MAX_TOPIC_WORDS, 250 * max(1, len(objectives))))


def format_sources(sources: list[ResearchSource], topic: TopicOutline) -> str:
    """The parts of each page this topic needs, not the first few thousand characters of all
    of them — which for a reference page is its introduction, every time."""
    if not sources:
        return "None."
    wanted = " ".join([topic.title, *topic.objectives])
    return passages_for(sources, wanted, CHARS_PER_TOPIC)


def covered_so_far(curriculum: Curriculum, outline: ChapterOutline, position: int) -> str:
    """Each topic is written by its own call with no memory of the others, so the only thing
    stopping topic 5 re-teaching topic 2 is being told what topic 2 did."""
    earlier = [
        f"- {item.number}. {item.title}"
        for item in curriculum.chapters
        if item.number < outline.number
    ]
    earlier += [
        f"- {outline.number}.{index} {topic.title}"
        for index, topic in enumerate(outline.topics[: position - 1], start=1)
    ]
    if not earlier:
        return (
            "This is the first topic of the course. Nothing has been taught yet, so introduce "
            "every term you use."
        )
    listed = "\n".join(earlier)
    return (
        "Already covered. Assume the learner has read this and do not explain it again — "
        f"refer back by number instead:\n{listed}"
    )


def coming_later(curriculum: Curriculum, outline: ChapterOutline, position: int) -> str:
    later = [
        f"- {outline.number}.{index} {topic.title}"
        for index, topic in enumerate(outline.topics[position:], start=position + 1)
    ]
    later += [
        f"- {item.number}. {item.title}"
        for item in curriculum.chapters
        if item.number > outline.number
    ]
    if not later:
        return (
            "This is the final topic of the course. Close the subject off rather than "
            "pointing forward to material that does not exist."
        )
    listed = "\n".join(later)
    return f"Reserved for later. Mention in passing at most, never teach:\n{listed}"


def format_issues(issues: list[str]) -> str:
    """Without this the rewrite is a fresh sample of the same prompt and comes back as weak."""
    if not issues:
        return ""
    listed = "\n".join(f"- {issue}" for issue in issues)
    return (
        "\n\nA reviewer rejected the previous draft of this chapter. Fix every one of "
        f"these that applies to this topic:\n{listed}"
    )


def build_prompt(
    request: LearningRequest,
    curriculum: Curriculum,
    outline: ChapterOutline,
    topic: TopicOutline,
    position: int,
    sources: list[ResearchSource],
    issues: list[str] | None = None,
) -> str:
    objectives = "\n".join(f"- {objective}" for objective in topic.objectives) or "- not stated"
    return (
        f"Course: {curriculum.title}\n"
        f"Skill: {request.skill}\n"
        f"Learner's level: {request.assumed_level}\n"
        f"Goal: {request.goal or 'not stated'}\n"
        f"Course language: {request.language}\n"
        f"Write all code in {request.assumed_programming_language}. The sources may show "
        f"other languages; translate the idea rather than switching language.\n"
        f"Target length: about {target_words(topic.objectives)} words across all parts.\n\n"
        f"Chapter {outline.number}: {outline.title}\n"
        f"Write topic {outline.number}.{position}: {topic.title}\n\n"
        f"By the end the learner must be able to:\n{objectives}\n\n"
        f"{covered_so_far(curriculum, outline, position)}\n\n"
        f"{coming_later(curriculum, outline, position)}\n\n"
        f"Sources you may draw on:\n{format_sources(sources, topic)}"
        f"{format_issues(issues or [])}"
    )


def build_wrap_prompt(outline: ChapterOutline, topics: list[Topic]) -> str:
    written = "\n\n".join(f"### {topic.label} {topic.title}\n{topic.what_it_is}" for topic in topics)
    objectives = "\n".join(f"- {objective}" for objective in outline.objectives) or "- not stated"
    return (
        f"Chapter {outline.number}: {outline.title}\n\n"
        f"The chapter promised the learner would be able to:\n{objectives}\n\n"
        f"These are the topics it ended up containing:\n\n{written}"
    )


def render_topic(topic: Topic) -> str:
    """The document structure is written here rather than asked for, so every topic carries
    the same parts in the same order and a reader can find them without reading around.

    Headings are h2 because the exporter demotes a chapter body by one level to nest it under
    the chapter's own heading.
    """
    blocks = [
        f"## {topic.label} {topic.title}",
        topic.what_it_is.strip(),
        f"**Why it matters.** {topic.why_it_matters.strip()}",
        topic.how_to_use.strip(),
    ]
    figure = render_diagram(topic.diagram)
    if figure:
        blocks.insert(1, figure)
    if topic.implementation.strip():
        blocks.append(f"**Implementation**\n\n{topic.implementation.strip()}")
    return "\n\n".join(block for block in blocks if block)


def render_body(topics: list[Topic]) -> str:
    return "\n\n".join(render_topic(topic) for topic in topics)


def assemble_topic(
    outline: ChapterOutline, topic: TopicOutline, position: int, draft: TopicDraft
) -> Topic:
    """Number and title come from the plan, not the draft, so a topic cannot drift away from
    the curriculum it was commissioned from."""
    return Topic(
        chapter_number=outline.number,
        number=position,
        title=topic.title,
        what_it_is=draft.what_it_is,
        why_it_matters=draft.why_it_matters,
        how_to_use=draft.how_to_use,
        implementation=draft.implementation,
        diagram=draft.diagram,
    )


async def write_topic(
    request: LearningRequest,
    curriculum: Curriculum,
    outline: ChapterOutline,
    topic: TopicOutline,
    position: int,
    sources: list[ResearchSource],
    issues: list[str] | None = None,
) -> Topic:
    response = await get_chapter_agent().run(
        build_prompt(request, curriculum, outline, topic, position, sources, issues)
    )
    draft: TopicDraft = response.value

    if not draft.what_it_is.strip() or not draft.how_to_use.strip():
        raise ValueError(
            f"chapter-agent returned an empty topic {outline.number}.{position}"
        )

    return assemble_topic(outline, topic, position, draft)


def plan_jobs(outlines: list[ChapterOutline]) -> list[_TopicJob]:
    """Flatten every chapter's topics into one list.

    Fanning out over chapters and again over their topics would multiply the two limits
    together, so the topics are the only gate.
    """
    return [
        _TopicJob(number=outline.number, outline=outline, topic=topic, position=position)
        for outline in outlines
        for position, topic in enumerate(outline.topics, start=1)
    ]


def group_by_chapter(jobs: list[_TopicJob], topics: list[Topic]) -> dict[int, list[Topic]]:
    """asyncio.gather preserves input order, so a chapter's topics come back in reading order."""
    grouped: dict[int, list[Topic]] = {}
    for job, topic in zip(jobs, topics):
        grouped.setdefault(job.number, []).append(topic)
    return grouped


async def wrap_chapters(
    outlines: list[ChapterOutline], grouped: dict[int, list[Topic]]
) -> list[Chapter]:
    """One cheap call per chapter for the things that can only be judged once it is whole."""

    async def wrap_one(outline: ChapterOutline) -> Chapter:
        topics = grouped.get(outline.number, [])
        draft: ChapterDraft = (
            await get_wrap_agent().run(build_wrap_prompt(outline, topics))
        ).value
        return Chapter(
            number=outline.number,
            title=outline.title,
            body_markdown=render_body(topics),
            topics=topics,
            key_points=draft.key_points,
            exercises=draft.exercises,
        )

    return await per_chapter(WRAP_AGENT_NAME, outlines, wrap_one, MAX_CONCURRENT_CHAPTERS)


async def write_outlines(
    request: LearningRequest,
    curriculum: Curriculum,
    outlines: list[ChapterOutline],
    sources: list[ResearchSource],
    issues: dict[int, list[str]] | None = None,
) -> list[Chapter]:
    jobs = plan_jobs(outlines)

    async def write_one(job: _TopicJob) -> Topic:
        return await write_topic(
            request,
            curriculum,
            job.outline,
            job.topic,
            job.position,
            sources,
            (issues or {}).get(job.number, []),
        )

    topics = await per_chapter(AGENT_NAME, jobs, write_one, MAX_CONCURRENT_CHAPTERS)
    return await wrap_chapters(outlines, group_by_chapter(jobs, topics))


async def write_chapters(
    request: LearningRequest, curriculum: Curriculum, sources: list[ResearchSource]
) -> list[Chapter]:
    return await write_outlines(request, curriculum, curriculum.chapters, sources)


async def rewrite_chapters(
    request: LearningRequest,
    curriculum: Curriculum,
    sources: list[ResearchSource],
    review: ReviewResult,
) -> list[Chapter]:
    """Rewrite only the chapters the review flagged, each told what was wrong with it."""
    targets = set(review.regenerate_chapters)
    outlines = [outline for outline in curriculum.chapters if outline.number in targets]
    return await write_outlines(
        request, curriculum, outlines, sources, review.chapter_issues
    )


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

