"""practice-agent — sets self-marked practice tasks for every chapter."""

from __future__ import annotations

import logging
from functools import lru_cache

from agent_framework import Agent, Executor, WorkflowContext, handler

from backend.agents.fanout import per_chapter
from backend.prompts.loader import load_prompt
from backend.services.foundry import get_chat_client
from backend.workflow.state import (
    Chapter,
    ChapterOutline,
    CourseState,
    Curriculum,
    LearningRequest,
    PracticeItem,
    PracticeSet,
    WorkflowStep,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "practice-agent"

# One model call per chapter, the same shape and the same rate limit as chapter-agent.
MAX_CONCURRENT_SETS = 4

# Practice exists to prove the objectives were met, so the count follows them.
MIN_TASKS = 2
MAX_TASKS = 4


@lru_cache
def get_practice_agent() -> Agent:
    return get_chat_client().as_agent(
        name=AGENT_NAME,
        instructions=load_prompt("practice"),
        default_options={"response_format": PracticeSet},
    )


def plan_task_count(objectives: list[str]) -> int:
    """One task per promise the chapter made, within bounds."""
    return max(MIN_TASKS, min(MAX_TASKS, len(objectives)))


def format_exercises(chapter: Chapter) -> str:
    """The chapter already set do-it-now tasks. Without this list the practice agent writes
    the same ones again in different words."""
    if not chapter.exercises:
        return "The chapter set no exercises of its own, so nothing is off limits."
    listed = "\n".join(f"- {exercise}" for exercise in chapter.exercises)
    return f"Already set inside the chapter. Do not repeat or rephrase these:\n{listed}"


def build_prompt(request: LearningRequest, outline: ChapterOutline, chapter: Chapter) -> str:
    objectives = "\n".join(f"- {objective}" for objective in outline.objectives) or "- not stated"
    key_points = "\n".join(f"- {point}" for point in chapter.key_points) or "- none"
    return (
        f"Skill: {request.skill}\n"
        f"Learner's level: {request.assumed_level}\n"
        f"Course language: {request.language}\n"
        f"Produce exactly {plan_task_count(outline.objectives)} tasks.\n\n"
        f"Chapter {chapter.number}: {chapter.title}\n\n"
        f"Objectives this chapter promised:\n{objectives}\n\n"
        f"Key points:\n{key_points}\n\n"
        f"{format_exercises(chapter)}\n\n"
        f"The chapter as the learner read it:\n{chapter.body_markdown}"
    )


def attach(chapter_number: int, practice: PracticeSet) -> list[PracticeItem]:
    """The chapter number is ours, so a task cannot be filed against the wrong chapter."""
    return [
        PracticeItem(
            chapter_number=chapter_number,
            kind=task.kind,
            prompt=task.prompt,
            solution=task.solution,
        )
        for task in practice.tasks
    ]


async def write_practice_set(
    request: LearningRequest, outline: ChapterOutline, chapter: Chapter
) -> list[PracticeItem]:
    response = await get_practice_agent().run(build_prompt(request, outline, chapter))
    practice: PracticeSet = response.value

    if not practice.tasks:
        raise ValueError(f"practice-agent returned no tasks for chapter {chapter.number}")

    return attach(chapter.number, practice)


async def set_practice(
    request: LearningRequest, curriculum: Curriculum, chapters: list[Chapter]
) -> list[PracticeItem]:
    outlines = {outline.number: outline for outline in curriculum.chapters}

    async def write_one(chapter: Chapter) -> list[PracticeItem]:
        return await write_practice_set(request, outlines[chapter.number], chapter)

    per_set = await per_chapter(AGENT_NAME, chapters, write_one, MAX_CONCURRENT_SETS)
    return [item for tasks in per_set for item in tasks]


class PracticeExecutor(Executor):
    """Graph node for practice-agent."""

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState]) -> None:
        assert state.request is not None and state.curriculum is not None
        state.practice = await set_practice(state.request, state.curriculum, state.chapters)
        state.mark(WorkflowStep.PRACTICE)
        await ctx.send_message(state)

