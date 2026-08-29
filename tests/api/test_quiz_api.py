"""Tests for taking a quiz.

The thing most worth pinning: the answer never travels outward, and a score is computed here
rather than accepted from the client.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api import quiz as quiz_api
from backend.main import app
from backend.models.course import StoredCourse
from backend.models.quiz import QuizAttempt
from backend.services.course_store import FileCourseStore
from backend.services.quiz_store import InMemoryQuizStore
from backend.workflow.state import CourseState, Quiz, QuizQuestion
from tests.conftest import as_user

client = TestClient(app)

USER = "priya@contoso.com"
COURSE = "11111111-1111-4111-8111-111111111111"
MINE = as_user(USER)
THEIRS = as_user("mallory")


def question(text: str, correct: int) -> QuizQuestion:
    return QuizQuestion(
        question=text,
        options=["alpha", "bravo", "charlie"],
        correct_index=correct,
        explanation=f"because {text}",
    )


def course_with(*quizzes: Quiz) -> StoredCourse:
    state = CourseState(job_id="j", user_id=USER, prompt="p")
    state.quizzes = list(quizzes)
    return StoredCourse(id=COURSE, user_id=USER, job_id="j", state=state)


@pytest.fixture(autouse=True)
def stores(monkeypatch, tmp_path):
    courses, attempts = FileCourseStore(tmp_path), InMemoryQuizStore()
    monkeypatch.setattr(quiz_api, "course_store", courses)
    monkeypatch.setattr(quiz_api, "quiz_store", attempts)
    return SimpleNamespace(courses=courses, attempts=attempts)


async def save(stores, *quizzes: Quiz) -> None:
    await stores.courses.save(course_with(*quizzes))


def chapter_quiz() -> Quiz:
    return Quiz(scope="Chapter 1", chapter_number=1, questions=[question("q1", 1), question("q2", 0)])


def final_quiz() -> Quiz:
    return Quiz(scope="Final", chapter_number=None, questions=[question("f1", 2)])


# --- delivery ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_answer_is_never_sent_to_the_learner(stores):
    """A quiz that ships its own answer key is not an assessment."""
    await save(stores, chapter_quiz())

    body = client.get(f"/quiz/{COURSE}", params={"chapter": 1}, headers=MINE).json()

    assert "correct_index" not in str(body)
    assert body["questions"][0]["options"] == ["alpha", "bravo", "charlie"]


@pytest.mark.asyncio
async def test_questions_are_numbered_from_one(stores):
    await save(stores, chapter_quiz())

    body = client.get(f"/quiz/{COURSE}", params={"chapter": 1}, headers=MINE).json()

    assert [q["number"] for q in body["questions"]] == [1, 2]


@pytest.mark.asyncio
async def test_asking_for_no_chapter_gets_the_final_assessment(stores):
    """None is a real value here, not a missing one."""
    await save(stores, chapter_quiz(), final_quiz())

    body = client.get(f"/quiz/{COURSE}", headers=MINE).json()

    assert body["scope"] == "Final" and body["chapter_number"] is None


@pytest.mark.asyncio
async def test_a_chapter_with_no_quiz_is_a_404(stores):
    await save(stores, chapter_quiz())

    assert (
        client.get(f"/quiz/{COURSE}", params={"chapter": 9}, headers=MINE).status_code == 404
    )


@pytest.mark.asyncio
async def test_someone_elses_course_is_not_found(stores):
    await save(stores, chapter_quiz())

    response = client.get(f"/quiz/{COURSE}", params={"chapter": 1}, headers=THEIRS)

    assert response.status_code == 404


def test_a_quiz_cannot_be_read_without_a_learner():
    """401 rather than the old 422: an anonymous caller is not a malformed request."""
    assert client.get(f"/quiz/{COURSE}").status_code == 401


# --- marking -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_score_is_computed_here_not_taken_from_the_client(stores):
    await save(stores, chapter_quiz())

    body = client.post(
        f"/quiz/{COURSE}/answers",
        params={"chapter": 1},
        headers=MINE,
        json={"answers": {"1": 1, "2": 0}, "percent": 0, "correct": 0},
    ).json()

    assert body["correct"] == 2 and body["percent"] == 100


@pytest.mark.asyncio
async def test_a_wrong_answer_comes_back_with_its_explanation(stores):
    await save(stores, chapter_quiz())

    body = client.post(
        f"/quiz/{COURSE}/answers",
        params={"chapter": 1},
        headers=MINE,
        json={"answers": {"1": 0}},
    ).json()

    first = body["answers"][0]
    assert first["correct"] is False and first["explanation"] == "because q1"


@pytest.mark.asyncio
async def test_an_unanswered_question_is_marked_wrong_not_dropped(stores):
    """Otherwise answering one question out of ten scores 100%."""
    await save(stores, chapter_quiz())

    body = client.post(
        f"/quiz/{COURSE}/answers",
        params={"chapter": 1},
        headers=MINE,
        json={"answers": {"1": 1}},
    ).json()

    assert body["total"] == 2 and body["correct"] == 1 and body["percent"] == 50
    assert body["answers"][1]["chosen_index"] is None


@pytest.mark.asyncio
async def test_an_option_that_does_not_exist_is_refused(stores):
    await save(stores, chapter_quiz())

    response = client.post(
        f"/quiz/{COURSE}/answers",
        params={"chapter": 1},
        headers=MINE,
        json={"answers": {"1": 7}},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_an_answer_to_a_question_that_does_not_exist_is_refused(stores):
    await save(stores, chapter_quiz())

    response = client.post(
        f"/quiz/{COURSE}/answers",
        params={"chapter": 1},
        headers=MINE,
        json={"answers": {"9": 0}},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_the_attempt_is_kept(stores):
    await save(stores, chapter_quiz())

    client.post(
        f"/quiz/{COURSE}/answers",
        params={"chapter": 1},
        headers=MINE,
        json={"answers": {"1": 1}},
    )

    kept = await stores.attempts.for_course(COURSE, USER)
    assert len(kept) == 1 and kept[0].correct == 1 and kept[0].total == 2


@pytest.mark.asyncio
async def test_another_learners_attempts_are_not_returned(stores):
    await stores.attempts.save(
        QuizAttempt(id="a", user_id=USER, course_id=COURSE, correct=1, total=1, percent=100)
    )

    assert await stores.attempts.for_course(COURSE, "mallory") == []
