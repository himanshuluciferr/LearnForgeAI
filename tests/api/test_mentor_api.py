"""Tests for the mentor endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.agents import mentor as mentor_module
from backend.api import mentor as mentor_api
from backend.main import app
from backend.models.course import StoredCourse
from backend.services.course_store import FileCourseStore
from backend.workflow.state import Chapter, CourseState, Curriculum, MentorAnswer
from tests.conftest import as_user

client = TestClient(app)

USER = "priya@contoso.com"
COURSE = "33333333-3333-4333-8333-333333333333"
MINE = as_user(USER)
THEIRS = as_user("mallory")


def stored_course() -> StoredCourse:
    state = CourseState(job_id="j", user_id=USER, prompt="p")
    state.curriculum = Curriculum(title="Operators", summary="s", chapters=[])
    state.chapters = [Chapter(number=1, title="One", body_markdown="reconcile loops")]
    return StoredCourse(id=COURSE, user_id=USER, job_id="j", state=state)


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    courses = FileCourseStore(tmp_path)
    monkeypatch.setattr(mentor_api, "course_store", courses)
    return courses


def answering(answer: MentorAnswer, monkeypatch):
    class StubAgent:
        async def run(self, prompt: str):
            return type("Response", (), {"value": answer})()

    monkeypatch.setattr(mentor_module, "get_mentor_agent", lambda: StubAgent())


# --- the endpoint --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_grounded_answer_comes_back_with_its_chapter(store, monkeypatch):
    await store.save(stored_course())
    answering(MentorAnswer(grounded=True, answer="It reconciles.", chapter_number=1), monkeypatch)

    body = client.post(
        f"/mentor/{COURSE}", headers=MINE, json={"question": "what is a controller?"}
    ).json()

    assert body["grounded"] is True and body["chapter_number"] == 1
    assert body["answer"] == "It reconciles."


@pytest.mark.asyncio
async def test_what_the_course_does_not_cover_is_said_plainly(store, monkeypatch):
    """The learner is told to look elsewhere rather than handed something plausible."""
    await store.save(stored_course())
    answering(MentorAnswer(grounded=False, answer=""), monkeypatch)

    body = client.post(
        f"/mentor/{COURSE}", headers=MINE, json={"question": "what is a mesh?"}
    ).json()

    assert body["grounded"] is False and "does not cover that" in body["answer"]


@pytest.mark.asyncio
async def test_another_learners_course_cannot_be_asked_about(store):
    await store.save(stored_course())

    response = client.post(
        f"/mentor/{COURSE}", headers=THEIRS, json={"question": "anything?"}
    )

    assert response.status_code == 404


def test_a_question_needs_a_learner():
    """401 rather than the old 422: an anonymous caller is not a malformed request."""
    assert client.post(f"/mentor/{COURSE}", json={"question": "anything?"}).status_code == 401


@pytest.mark.asyncio
async def test_an_empty_question_is_refused_by_the_schema(store):
    await store.save(stored_course())

    response = client.post(f"/mentor/{COURSE}", headers=MINE, json={"question": " "})

    assert response.status_code == 422


