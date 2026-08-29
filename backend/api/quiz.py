"""Quiz delivery and answer-scoring endpoints.

The learner is never sent `correct_index`, and the answer is never taken from the client:
marking happens here, against the stored course, so a score cannot be forged.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from backend.api.deps import CurrentLearner
from backend.models.quiz import QuizAttempt
from backend.schemas.quiz import (
    AnswerSubmission,
    MarkedAnswer,
    QuizOut,
    QuizQuestionOut,
    QuizResult,
)
from backend.services.course_store import course_store
from backend.services.quiz_store import quiz_store
from backend.workflow.state import Quiz

router = APIRouter(prefix="/quiz", tags=["quiz"])


def find_quiz(quizzes: list[Quiz], chapter: int | None) -> Quiz | None:
    """`chapter=None` is the final assessment — a real value, not a missing one."""
    return next((quiz for quiz in quizzes if quiz.chapter_number == chapter), None)


def mark(quiz: Quiz, chosen: dict[int, int]) -> list[MarkedAnswer]:
    """An unanswered question is marked wrong, not skipped: a score is out of the whole quiz."""
    return [
        MarkedAnswer(
            number=number,
            chosen_index=chosen.get(number),
            correct_index=question.correct_index,
            correct=chosen.get(number) == question.correct_index,
            explanation=question.explanation,
        )
        for number, question in enumerate(quiz.questions, start=1)
    ]


async def load_quiz(course_id: str, user_id: str, chapter: int | None) -> Quiz:
    course = await course_store.get(course_id, user_id)
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    quiz = find_quiz(course.state.quizzes, chapter)
    if quiz is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz not found")
    return quiz


@router.get("/{course_id}")
async def get_quiz(
    course_id: str, learner: CurrentLearner, chapter: int | None = None
) -> QuizOut:
    quiz = await load_quiz(course_id, learner.user_id, chapter)
    return QuizOut(
        course_id=course_id,
        scope=quiz.scope,
        chapter_number=quiz.chapter_number,
        questions=[
            QuizQuestionOut(number=number, question=question.question, options=question.options)
            for number, question in enumerate(quiz.questions, start=1)
        ],
    )


@router.post("/{course_id}/answers", status_code=status.HTTP_201_CREATED)
async def submit_answers(
    course_id: str,
    submission: AnswerSubmission,
    learner: CurrentLearner,
    chapter: int | None = None,
) -> QuizResult:
    user_id = learner.user_id
    quiz = await load_quiz(course_id, user_id, chapter)
    for number, choice in submission.answers.items():
        question = quiz.questions[number - 1] if 1 <= number <= len(quiz.questions) else None
        if question is None or not 0 <= choice < len(question.options):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Answer {number}={choice} is not one of that question's options",
            )

    answers = mark(quiz, submission.answers)
    correct = sum(1 for answer in answers if answer.correct)
    total = len(answers)
    attempt = QuizAttempt(
        id=str(uuid4()),
        user_id=user_id,
        course_id=course_id,
        chapter_number=quiz.chapter_number,
        correct=correct,
        total=total,
        percent=round(100 * correct / total) if total else 0,
        answers=answers,
    )
    await quiz_store.save(attempt)
    return QuizResult(
        course_id=course_id,
        chapter_number=quiz.chapter_number,
        correct=correct,
        total=total,
        percent=attempt.percent,
        answers=answers,
    )
