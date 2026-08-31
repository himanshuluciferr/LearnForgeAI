"""mentor-agent — answers questions grounded in the user's generated course.

Retrieval-augmented, and mostly over text we already hold: the passages skill selects the part
of a corpus a query needs, and the course and the pages it was written from are both on the
state. So the common question costs one model call and no network.

When the stored material does not reach the question, the mentor goes and reads more — but
only if the question is about this course's subject. That gate is the whole safety of it.
Every retriever answers an off-corpus question with its nearest neighbour rather than with
nothing, so searching "how do I configure BGP timers" finds real, authoritative Cisco pages,
and a mentor that read them would teach a Kubernetes learner networking and imply the course
had covered it.
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

from agent_framework import Agent

from backend.agents.chapter import CHARS_PER_TOPIC
from backend.agents.research import classify
from backend.prompts.loader import load_prompt
from backend.services.foundry import get_chat_client
from backend.services.page_fetch import fetch_documents
from backend.services.retrieval import get_retriever
from backend.services.web_search import search_web
from backend.skills.passages.skill import passages_for
from backend.workflow.state import (
    Chapter,
    CourseState,
    MentorAnswer,
    ResearchSource,
    ResourceKind,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "mentor-agent"

# Reuses the writer's per-topic budget rather than inventing a number: a question is about
# roughly one topic's worth of material, and a second constant would be a second thing to tune.
CHARS_PER_ANSWER = CHARS_PER_TOPIC

# A lookup is a search plus fetches, and the learner is waiting in a chat window rather than
# polling a job. Small, and capped by the clock as well as the count.
MAX_LOOKUP_HITS = 6
MAX_LOOKUP_SOURCES = 3
LOOKUP_SECONDS = 45

NOT_COVERED = (
    "The course does not cover that. Ask me something it does and I can point you at the "
    "chapter, or start a course on it and I will go and read up."
)


@lru_cache
def get_mentor_agent() -> Agent:
    return get_chat_client().as_agent(
        name=AGENT_NAME,
        instructions=load_prompt("mentor"),
        default_options={"response_format": MentorAnswer},
    )


def as_sources(chapters: list[Chapter]) -> list[ResearchSource]:
    """The course made searchable by the same selector the sources use.

    The url is what `render` groups and labels blocks by, so each chapter needs its own or two
    chapters would arrive merged into one unattributable block.
    """
    return [
        ResearchSource(
            title=f"Chapter {chapter.number}: {chapter.title}",
            url=f"chapter-{chapter.number}",
            kind=ResourceKind.DOCS,
            text=chapter.body_markdown,
        )
        for chapter in chapters
    ]


async def build_prompt(
    question: str, state: CourseState, extra: str = "", where: dict[str, str] | None = None
) -> str:
    """What the course holds, then anything freshly read, then the question — last, so a long
    corpus cannot push it out of sight.

    Retrieval goes through the retriever rather than the selector directly, so an indexed
    course is searched and any other falls back to scanning what we hold.
    """
    retriever = get_retriever()
    keys = where or {}
    # One retrieval over everything, at the budget the two calls used to share. The index is
    # searched by course rather than by corpus, so asking it a second time with the research
    # sources returned the same passages again: two embeddings, two queries, and half the
    # prompt spent repeating itself under a different heading.
    corpus = [*as_sources(state.chapters), *state.research]
    passages = await retriever.passages(question, corpus, CHARS_PER_ANSWER * 2, **keys)
    title = state.curriculum.title if state.curriculum else "this course"
    looked_up = f"\n\nRead just now, because the course did not cover it:\n{extra}" if extra else ""
    return (
        f"Course: {title}\n\n"
        f"From the course and the pages it was written from:\n{passages}"
        f"{looked_up}\n\n"
        f'The learner asks:\n"""\n{question}\n"""'
    )


def chapter_in(answer: MentorAnswer, state: CourseState) -> int | None:
    """A chapter number that is not in this course is worse than none: it sends the learner to
    re-read something that does not exist."""
    numbers = {chapter.number for chapter in state.chapters}
    return answer.chapter_number if answer.chapter_number in numbers else None


def settle(answer: MentorAnswer, state: CourseState, looked_up: bool = False) -> MentorAnswer:
    """An empty answer is not grounded whatever the model said; the two disagreeing would show
    the learner a blank reply and call it an answer."""
    grounded = answer.grounded and bool(answer.answer.strip())
    return MentorAnswer(
        grounded=grounded,
        answer=answer.answer.strip() if grounded else "",
        # Nothing freshly read came from a chapter, so there is nothing to send them back to.
        chapter_number=None if looked_up or not grounded else chapter_in(answer, state),
        about_the_subject=answer.about_the_subject,
        look_up=answer.look_up,
    )


async def read_more(query: str) -> list[ResearchSource]:
    """Search and fetch, the same path research-agent uses, so a page reaches the mentor only
    through the SSRF checks and the text extractor rather than as a url a model typed."""
    hits = await search_web(query)
    documents = await fetch_documents(hits[:MAX_LOOKUP_HITS], MAX_LOOKUP_SOURCES)
    return [
        ResearchSource(
            title=document.title, url=document.url, kind=classify(document.url), text=document.text
        )
        for document in documents
    ]


async def look_up(
    answer: MentorAnswer, question: str, state: CourseState, where: dict[str, str] | None = None
) -> MentorAnswer:
    """Second pass over pages fetched for this question. Still grounded: if what came back does
    not answer it either, the refusal stands rather than being talked around."""
    try:
        fresh = await asyncio.wait_for(read_more(answer.look_up), timeout=LOOKUP_SECONDS)
    except (TimeoutError, asyncio.TimeoutError):
        logger.info("mentor-agent: looking up %r ran out of time", answer.look_up[:60])
        return settle(MentorAnswer(grounded=False), state)
    except Exception:
        logger.exception("mentor-agent: looking up %r failed", answer.look_up[:60])
        return settle(MentorAnswer(grounded=False), state)

    if not fresh:
        return settle(MentorAnswer(grounded=False), state)

    logger.info("mentor-agent: read %d page(s) for %r", len(fresh), answer.look_up[:60])
    # Freshly fetched pages are not in the index, so they are always selected lexically.
    extra = passages_for(fresh, question, CHARS_PER_ANSWER)
    response = await get_mentor_agent().run(await build_prompt(question, state, extra, where))
    return settle(response.value, state, looked_up=True)


async def answer_question(
    question: str,
    state: CourseState,
    allow_lookup: bool = True,
    where: dict[str, str] | None = None,
) -> MentorAnswer:
    if not question.strip():
        return MentorAnswer(grounded=False)
    if not state.chapters and not state.research:
        return MentorAnswer(grounded=False)

    response = await get_mentor_agent().run(await build_prompt(question, state, where=where))
    answer = settle(response.value, state)
    if answer.grounded:
        return answer

    # Reading more is for a question this subject should be able to answer. Anything else gets
    # the refusal: a search would find real pages about the wrong thing and sound authoritative.
    if not (allow_lookup and answer.about_the_subject and answer.look_up.strip()):
        logger.info("mentor-agent: nothing in the course answered %r", question[:80])
        return answer
    return await look_up(answer, question, state, where)
