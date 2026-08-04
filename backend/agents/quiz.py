"""quiz-agent — writes machine-marked multiple-choice quizzes."""

from __future__ import annotations

import logging
import random
from functools import lru_cache

from agent_framework import Agent, Executor, WorkflowContext, handler

from backend.agents.fanout import per_chapter
from backend.prompts.loader import load_prompt
from backend.services.foundry import get_chat_client
from backend.workflow.state import (
    Chapter,
    CourseState,
    LearningRequest,
    Quiz,
    QuizDraft,
    QuizQuestion,
    QuizSet,
    WorkflowStep,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "quiz-agent"

MAX_CONCURRENT_QUIZZES = 4

# A quiz checks the takeaways, so the count follows the chapter's key points.
MIN_QUESTIONS = 3
MAX_QUESTIONS = 5

# The final assessment spans the course: roughly one question per chapter.
MIN_FINAL_QUESTIONS = 5
MAX_FINAL_QUESTIONS = 12

# Four options: fewer makes guessing pay, more pads the question with weak distractors.
MIN_DISTRACTORS = 2
MAX_DISTRACTORS = 3

FINAL_SCOPE = "Final assessment"


@lru_cache
def get_quiz_agent() -> Agent:
    return get_chat_client().as_agent(
        name=AGENT_NAME,
        instructions=load_prompt("quiz"),
        default_options={"response_format": QuizSet},
    )


def plan_question_count(chapter: Chapter) -> int:
    """Practice checks the objectives the chapter promised; the quiz checks the key points
    it landed. Different anchors keep the two from testing the same thing."""
    return max(MIN_QUESTIONS, min(MAX_QUESTIONS, len(chapter.key_points)))


def plan_final_count(chapters: list[Chapter]) -> int:
    return max(MIN_FINAL_QUESTIONS, min(MAX_FINAL_QUESTIONS, len(chapters)))


def usable_distractors(draft: QuizDraft) -> list[str]:
    """Drop distractors that repeat the right answer or each other.

    A duplicate means either two correct answers or a wasted slot, and both are worse for the
    learner than a shorter question.
    """
    seen = {draft.correct_answer.strip().lower()}
    kept: list[str] = []
    for distractor in draft.distractors:
        key = distractor.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(distractor)
    return kept[:MAX_DISTRACTORS]


def assemble(draft: QuizDraft) -> QuizQuestion | None:
    """Build the options and locate the answer, rather than trusting an index.

    The shuffle is seeded on the question text so a question always renders the same way,
    while still scattering the answers — models place the right one first far more often
    than chance.
    """
    if not draft.question.strip() or not draft.correct_answer.strip():
        return None

    distractors = usable_distractors(draft)
    if len(distractors) < MIN_DISTRACTORS:
        return None

    options = [draft.correct_answer, *distractors]
    random.Random(draft.question).shuffle(options)

    return QuizQuestion(
        question=draft.question,
        options=options,
        correct_index=options.index(draft.correct_answer),
        explanation=draft.explanation,
    )


def build_quiz(scope: str, quiz_set: QuizSet) -> Quiz:
    questions = [question for question in map(assemble, quiz_set.questions) if question]

    # A short quiz is degraded; a quiz nobody can take is broken.
    if not questions:
        raise ValueError(f"quiz-agent returned no usable questions for {scope}")

    dropped = len(quiz_set.questions) - len(questions)
    if dropped:
        logger.warning("quiz-agent: dropped %d unusable question(s) from %s", dropped, scope)

    return Quiz(scope=scope, questions=questions)


def build_chapter_prompt(request: LearningRequest, chapter: Chapter) -> str:
    key_points = "\n".join(f"- {point}" for point in chapter.key_points) or "- none stated"
    return (
        f"Skill: {request.skill}\n"
        f"Learner's level: {request.experience}\n"
        f"Course language: {request.language}\n"
        f"Write exactly {plan_question_count(chapter)} questions.\n\n"
        f"Chapter {chapter.number}: {chapter.title}\n\n"
        f"Key points the learner should now hold:\n{key_points}\n\n"
        f"The chapter as the learner read it:\n{chapter.body_markdown}"
    )


def build_final_prompt(request: LearningRequest, chapters: list[Chapter]) -> str:
    """The final assessment gets titles and key points, not full prose — its job is to test
    across chapters, and the whole course would not fit in one call anyway."""
    outline = "\n\n".join(
        "\n".join(
            [f"Chapter {chapter.number}: {chapter.title}"]
            + [f"- {point}" for point in chapter.key_points]
        )
        for chapter in chapters
    )
    return (
        f"Skill: {request.skill}\n"
        f"Learner's level: {request.experience}\n"
        f"Course language: {request.language}\n"
        f"Write exactly {plan_final_count(chapters)} questions.\n\n"
        "This is the final assessment for the whole course. Spread the questions across the "
        "chapters below, and prefer questions that need two chapters at once over questions "
        "a single chapter would answer.\n\n"
        f"{outline}"
    )


async def write_chapter_quiz(request: LearningRequest, chapter: Chapter) -> Quiz:
    response = await get_quiz_agent().run(build_chapter_prompt(request, chapter))
    return build_quiz(f"Chapter {chapter.number}: {chapter.title}", response.value)


async def write_final_quiz(request: LearningRequest, chapters: list[Chapter]) -> Quiz:
    response = await get_quiz_agent().run(build_final_prompt(request, chapters))
    return build_quiz(FINAL_SCOPE, response.value)


async def build_quizzes(request: LearningRequest, chapters: list[Chapter]) -> list[Quiz]:
    async def write_one(chapter: Chapter) -> Quiz:
        return await write_chapter_quiz(request, chapter)

    quizzes = await per_chapter(AGENT_NAME, chapters, write_one, MAX_CONCURRENT_QUIZZES)
    quizzes.append(await write_final_quiz(request, chapters))
    return quizzes


class QuizExecutor(Executor):
    """Graph node for quiz-agent."""

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState]) -> None:
        assert state.request is not None
        state.quizzes = await build_quizzes(state.request, state.chapters)
        state.mark(WorkflowStep.QUIZ)
        await ctx.send_message(state)
