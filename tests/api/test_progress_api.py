"""Tests for learner progress.

Progress stores the bare fact — which chapters were read — and derives every number from it,
so the tests pin the derivation rather than a stored total.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api import progress as progress_api
from backend.main import app
from backend.models.course import StoredCourse
from backend.models.quiz import QuizAttempt
from backend.services.course_store import FileCourseStore
from backend.services.progress_store import InMemoryProgressStore
from backend.services.quiz_store import InMemoryQuizStore
from backend.workflow.state import Chapter, CourseState, Curriculum
from tests.conftest import as_user

client = TestClient(app)

USER = "priya@contoso.com"
COURSE = "22222222-2222-4222-8222-222222222222"
MINE = as_user(USER)
THEIRS = as_user("mallory")


def chapter(number: int) -> Chapter:
    return Chapter(number=number, title=f"Chapter {number}", body_markdown="body")


def stored_course(count: int = 3) -> StoredCourse:
    state = CourseState(job_id="j", user_id=USER, prompt="p")
    state.curriculum = Curriculum(title="A Course", summary="s", chapters=[])
    state.chapters = [chapter(n) for n in range(1, count + 1)]
    return StoredCourse(id=COURSE, user_id=USER, job_id="j", state=state)


@pytest.fixture(autouse=True)
def stores(monkeypatch, tmp_path):
    courses, progress, quizzes = (
        FileCourseStore(tmp_path),
        InMemoryProgressStore(),
        InMemoryQuizStore(),
    )
    monkeypatch.setattr(progress_api, "course_store", courses)
    monkeypatch.setattr(progress_api, "progress_store", progress)
    monkeypatch.setattr(progress_api, "quiz_store", quizzes)
    return SimpleNamespace(courses=courses, progress=progress, quizzes=quizzes)


def read(number: int) -> dict:
    return client.put(f"/progress/{COURSE}/chapters/{number}", headers=MINE).json()


# --- what a learner sees -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_new_course_starts_at_nothing_read(stores):
    await stores.courses.save(stored_course())

    body = client.get(f"/progress/{COURSE}", headers=MINE).json()

    assert body["chapters_read"] == 0 and body["percent"] == 0
    assert body["next_chapter"] == 1 and body["title"] == "A Course"


@pytest.mark.asyncio
async def test_the_percentage_follows_the_chapters_rather_than_being_stored(stores):
    await stores.courses.save(stored_course(4))

    read(1)
    body = read(2)

    assert body["chapters_read"] == 2 and body["percent"] == 50


@pytest.mark.asyncio
async def test_next_chapter_is_the_first_unread_not_the_last_read(stores):
    """A learner who jumps ahead is still owed the chapter they skipped."""
    await stores.courses.save(stored_course(3))

    body = read(2)

    assert body["next_chapter"] == 1


@pytest.mark.asyncio
async def test_finishing_the_last_chapter_leaves_nowhere_to_go(stores):
    await stores.courses.save(stored_course(2))

    read(1)
    body = read(2)

    assert body["percent"] == 100 and body["next_chapter"] is None


@pytest.mark.asyncio
async def test_reading_a_chapter_twice_is_not_an_error(stores):
    await stores.courses.save(stored_course(3))

    read(1)
    body = read(1)

    assert body["chapters_read"] == 1


@pytest.mark.asyncio
async def test_a_chapter_the_course_does_not_have_is_a_404(stores):
    await stores.courses.save(stored_course(2))

    response = client.put(f"/progress/{COURSE}/chapters/9", headers=MINE)

    assert response.status_code == 404


# --- quiz scores ride along ----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_best_quiz_score_is_shown_against_its_chapter(stores):
    await stores.courses.save(stored_course(2))
    for percent in (40, 90, 60):
        await stores.quizzes.save(
            QuizAttempt(
                id=f"a{percent}", user_id=USER, course_id=COURSE, chapter_number=1,
                correct=1, total=1, percent=percent,
            )
        )

    chapters = client.get(f"/progress/{COURSE}", headers=MINE).json()["chapters"]

    assert chapters[0]["best_quiz_percent"] == 90
    assert chapters[1]["best_quiz_percent"] is None


@pytest.mark.asyncio
async def test_a_zero_score_is_a_score_not_a_missing_one(stores):
    await stores.courses.save(stored_course(1))
    await stores.quizzes.save(
        QuizAttempt(
            id="a", user_id=USER, course_id=COURSE, chapter_number=1,
            correct=0, total=3, percent=0,
        )
    )

    chapters = client.get(f"/progress/{COURSE}", headers=MINE).json()["chapters"]

    assert chapters[0]["best_quiz_percent"] == 0


# --- ownership -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_another_learners_course_is_not_found(stores):
    await stores.courses.save(stored_course())

    assert client.get(f"/progress/{COURSE}", headers=THEIRS).status_code == 404


def test_progress_cannot_be_read_without_a_learner():
    """401 rather than the old 422: an anonymous caller is not a malformed request."""
    assert client.get(f"/progress/{COURSE}").status_code == 401


@pytest.mark.asyncio
async def test_one_learners_progress_is_not_anothers(stores):
    await stores.courses.save(stored_course(2))
    await stores.courses.save(
        StoredCourse(id=COURSE, user_id="mallory", job_id="j", state=stored_course(2).state)
    )

    read(1)
    theirs = client.get(f"/progress/{COURSE}", headers=THEIRS).json()

    assert theirs["chapters_read"] == 0

def test_progress_reads_the_projection_not_the_whole_course():
    """Opening a course did a 405 KB projected read for the document and a 618 KB full read
    for progress, so the reader paid for the course twice."""
    import inspect  # noqa: PLC0415

    from backend.api import progress as progress_api  # noqa: PLC0415

    body = inspect.getsource(progress_api.load_course)

    assert "for_display" in body
    assert "course_store.get" not in body
