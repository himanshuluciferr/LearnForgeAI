"""mentor-agent — answers questions grounded in the user's generated course.

Retrieval-augmented, but nothing new was needed to do it: the passages skill already selects
the part of a corpus a query needs, and the course and the pages it was written from are both
stored on the state. So this is one model call over text we already hold — no embeddings, no
second index, no re-fetching.

The course is searched ahead of the sources deliberately. A learner asking about chapter 3
should be answered in the words they read, not from a page they never saw; the sources are
there for the questions the course raises but does not settle.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from agent_framework import Agent

from backend.agents.chapter import CHARS_PER_TOPIC
from backend.prompts.loader import load_prompt
from backend.services.foundry import get_chat_client
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


def build_prompt(question: str, state: CourseState) -> str:
    """The course first, then what it was written from, then the question — last, so a long
    corpus cannot push it out of sight."""
    course = passages_for(as_sources(state.chapters), question, CHARS_PER_ANSWER)
    sources = passages_for(state.research, question, CHARS_PER_ANSWER)
    title = state.curriculum.title if state.curriculum else "this course"
    return (
        f"Course: {title}\n\n"
        f"From the course itself:\n{course}\n\n"
        f"From the pages the course was written from:\n{sources}\n\n"
        f'The learner asks:\n"""\n{question}\n"""'
    )


def chapter_in(answer: MentorAnswer, state: CourseState) -> int | None:
    """A chapter number that is not in this course is worse than none: it sends the learner to
    re-read something that does not exist."""
    numbers = {chapter.number for chapter in state.chapters}
    return answer.chapter_number if answer.chapter_number in numbers else None


async def answer_question(question: str, state: CourseState) -> MentorAnswer:
    if not question.strip():
        return MentorAnswer(grounded=False, answer="")
    if not state.chapters and not state.research:
        return MentorAnswer(grounded=False, answer="")

    response = await get_mentor_agent().run(build_prompt(question, state))
    answer: MentorAnswer = response.value

    # An empty answer is not grounded whatever the model said, and the two disagreeing would
    # show the learner a blank reply.
    grounded = answer.grounded and bool(answer.answer.strip())
    if not grounded:
        logger.info("mentor-agent: nothing in the course answered %r", question[:80])
    return MentorAnswer(
        grounded=grounded,
        answer=answer.answer.strip() if grounded else "",
        chapter_number=chapter_in(answer, state) if grounded else None,
    )
