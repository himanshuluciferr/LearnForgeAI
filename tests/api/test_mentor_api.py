"""Tests for the mentor endpoint and how the bot reaches it."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.agents import mentor as mentor_module
from backend.api import mentor as mentor_api
from backend.main import app
from backend.models.course import StoredCourse
from backend.services.course_store import FileCourseStore
from backend.workflow.state import Chapter, CourseState, Curriculum, MentorAnswer
from teams_bot.backend_client import BackendClient
from teams_bot.commands import Intent, read
from teams_bot.handlers import message_handler
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


# --- how the bot decides a message is a question --------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "what is a CRD?",
        "why does the controller requeue",
        "how do I write a reconcile loop?",
        "does an operator need a CRD?",
        "can I use kubebuilder",
        "is the operator pattern only for kubernetes?",
    ],
)
def test_a_question_goes_to_the_mentor_not_to_a_new_course(said):
    """Without this, "what is a CRD?" starts a twenty-minute build instead of being answered."""
    assert read(said).intent is Intent.MENTOR


@pytest.mark.parametrize(
    "said",
    ["teach me kubernetes operators", "Kubernetes operators", "rust ownership", "learn git rebase"],
)
def test_a_statement_is_still_a_request_to_learn(said):
    assert read(said).intent is Intent.TEACH


def test_progress_still_wins_over_looking_like_a_question():
    assert read("how am I doing?").intent is Intent.PROGRESS


# --- the bot's reply ------------------------------------------------------------------


def client_for(handler) -> BackendClient:
    return BackendClient(
        base_url="http://backend", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


@pytest.mark.asyncio
async def test_asking_with_no_course_says_so_rather_than_failing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    reply = await message_handler.handle("what is a CRD?", USER, client_for(handler))

    assert "you have none yet" in reply.text


@pytest.mark.asyncio
async def test_the_answer_points_at_the_chapter_to_reread():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/courses":
            return httpx.Response(200, json=[{"course_id": COURSE}])
        return httpx.Response(
            200,
            json={
                "course_id": COURSE,
                "question": "q",
                "answer": "It reconciles.",
                "grounded": True,
                "chapter_number": 3,
            },
        )

    reply = await message_handler.handle("what is a controller?", USER, client_for(handler))

    assert "It reconciles." in reply.text and "Chapter 3" in reply.text


@pytest.mark.asyncio
async def test_a_refusal_is_shown_without_a_chapter_to_reread():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/courses":
            return httpx.Response(200, json=[{"course_id": COURSE}])
        return httpx.Response(
            200,
            json={
                "course_id": COURSE,
                "question": "q",
                "answer": "The course does not cover that.",
                "grounded": False,
                "chapter_number": None,
            },
        )

    reply = await message_handler.handle("what is a mesh?", USER, client_for(handler))

    assert "Chapter" not in reply.text


@pytest.mark.asyncio
async def test_the_question_reaches_the_backend_as_the_learner_typed_it():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/courses":
            return httpx.Response(200, json=[{"course_id": COURSE}])
        seen.append(json.loads(request.content)["question"])
        return httpx.Response(
            200,
            json={"course_id": COURSE, "question": "q", "answer": "a", "grounded": True},
        )

    await message_handler.handle("why does it requeue?", USER, client_for(handler))

    assert seen == ["why does it requeue?"]
