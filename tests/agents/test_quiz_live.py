"""Live tests for quiz-agent. Opt in with `pytest -m live`. Three real model calls."""

import pytest
import pytest_asyncio

from backend.agents.quiz import FINAL_SCOPE, build_quizzes, plan_question_count
from backend.config.settings import get_settings
from tests.agents.test_practice_live import CHAPTERS, REQUEST

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def quizzes():
    """One live run of the whole step, shared by every assertion below."""
    if not get_settings().foundry_project_endpoint:
        pytest.skip("FOUNDRY_PROJECT_ENDPOINT is not set")

    return await build_quizzes(REQUEST, CHAPTERS)


async def test_each_chapter_gets_a_quiz_and_the_course_gets_a_final(quizzes):
    assert [quiz.scope for quiz in quizzes] == [
        "Chapter 1: Rebasing a branch onto main",
        "Chapter 2: Resolving conflicts during a rebase",
        FINAL_SCOPE,
    ]


async def test_the_marked_answer_is_the_answer_the_model_meant(quizzes):
    for quiz in quizzes:
        for question in quiz.questions:
            assert 0 <= question.correct_index < len(question.options)


async def test_no_question_ships_a_duplicated_option(quizzes):
    for quiz in quizzes:
        for question in quiz.questions:
            lowered = [option.strip().lower() for option in question.options]
            assert len(set(lowered)) == len(lowered), question.options


async def test_every_question_offers_a_real_choice(quizzes):
    for quiz in quizzes:
        for question in quiz.questions:
            assert len(question.options) >= 3, question.options


async def test_the_question_count_is_the_one_we_asked_for(quizzes):
    for chapter, quiz in zip(CHAPTERS, quizzes):
        assert len(quiz.questions) <= plan_question_count(chapter)


async def test_no_option_is_a_cop_out(quizzes):
    banned = ("all of the above", "none of the above", "both a and b", "all of these")
    offenders = [
        option
        for quiz in quizzes
        for question in quiz.questions
        for option in question.options
        if any(text in option.lower() for text in banned)
    ]

    assert offenders == [], offenders


async def test_questions_stand_on_their_own_without_the_text(quizzes):
    """A question that refers to the document is testing reading, not knowledge."""
    banned = ("according to the chapter", "in this course", "as stated", "the text above")
    offenders = [
        question.question
        for quiz in quizzes
        for question in quiz.questions
        if any(text in question.question.lower() for text in banned)
    ]

    assert offenders == [], offenders


async def test_nothing_refers_to_option_positions(quizzes):
    """We shuffle after the model writes, so any positional reference would now be a lie."""
    banned = ("option a", "option b", "option c", "option d", "the first option", "choice a")
    offenders = [
        question.explanation
        for quiz in quizzes
        for question in quiz.questions
        if any(text in question.explanation.lower() for text in banned)
    ]

    assert offenders == [], offenders


async def test_the_right_answer_is_not_usually_the_longest_one(quizzes):
    """Length is the classic giveaway. Unprompted this ran at 10/11, which lets a learner
    who knows nothing score 91% by picking the long option; the prompt now forbids
    qualifying the answer, which brought it to 2/11. Half is a generous ceiling.
    """
    questions = [question for quiz in quizzes for question in quiz.questions]

    longest_is_correct = [
        question
        for question in questions
        if question.options[question.correct_index] == max(question.options, key=len)
    ]

    assert len(longest_is_correct) * 2 < len(questions), (
        f"{len(longest_is_correct)}/{len(questions)} answers were the longest option"
    )


async def test_every_question_explains_itself(quizzes):
    for quiz in quizzes:
        for question in quiz.questions:
            assert len(question.explanation.split()) >= 5, question.explanation
