"""Tests for the course document.

The endpoint used to return the whole workflow state. The tests that matter here are the
ones asserting what is *absent* from the projection, built from a course that genuinely
contains every secret — a guard against leaking is worthless if the fixture has nothing
to leak.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api import course as course_api
from backend.main import app
from backend.models.course import StoredCourse
from backend.schemas.document import as_document
from backend.services.course_store import FileCourseStore
from backend.workflow.state import (
    Chapter,
    ChapterDiagram,
    CourseState,
    Curriculum,
    DiagramEdge,
    DiagramKind,
    ExperienceLevel,
    PracticeItem,
    PracticeKind,
    Project,
    PublishedCourse,
    Quiz,
    QuizQuestion,
    ResearchSource,
    ResourceKind,
    Topic,
)
from tests.conftest import as_user

client = TestClient(app)

USER = "priya@contoso.com"
COURSE = "44444444-4444-4444-8444-444444444444"
MINE = as_user(USER)
THEIRS = as_user("mallory")

SECRET_ANSWER = "the-answer-is-charlie"
SECRET_RESEARCH = "internal-research-note"


def topic(number: int) -> Topic:
    return Topic(
        chapter_number=1,
        number=number,
        title=f"Topic {number}",
        what_it_is="what it is",
        why_it_matters="why it matters",
        how_to_use="how to use",
        implementation="print('hi')",
        diagram=ChapterDiagram(
            kind=DiagramKind.FLOW,
            title="How it flows",
            nodes=["A", "B"],
            edges=[DiagramEdge(source="A", target="B", label="then")],
        ),
    )


def full_course() -> StoredCourse:
    """Everything a real course carries, including the parts that must not come back."""
    state = CourseState(job_id="j", user_id=USER, prompt="teach me operators")
    state.curriculum = Curriculum(title="Operators", summary="A course about operators", chapters=[])
    state.chapters = [
        Chapter(
            number=1,
            title="One",
            body_markdown="rendered markdown",
            topics=[topic(1), topic(2)],
            key_points=["a point"],
            exercises=["an exercise"],
        ),
        Chapter(number=2, title="Two", body_markdown="more markdown", topics=[topic(1)]),
    ]
    state.practice = [
        PracticeItem(chapter_number=1, kind=PracticeKind.APPLY, prompt="do it", solution="done"),
        PracticeItem(chapter_number=2, kind=PracticeKind.RECALL, prompt="recall", solution="ok"),
    ]
    state.projects = [
        Project(
            level=ExperienceLevel.BEGINNER,
            title="A Project",
            summary="builds a thing",
            features=["does a thing"],
            folder_structure="src/",
            milestones=["ship it"],
            stretch_goals=["ship it twice"],
        )
    ]
    state.quizzes = [
        Quiz(
            scope="Chapter 1",
            chapter_number=1,
            questions=[
                QuizQuestion(
                    question="which one?",
                    options=["alpha", "bravo", SECRET_ANSWER],
                    correct_index=2,
                    explanation="because",
                )
            ],
        ),
        Quiz(
            scope="Final",
            chapter_number=None,
            questions=[
                QuizQuestion(
                    question="final?", options=["a", "b"], correct_index=0, explanation="e"
                )
            ],
        ),
    ]
    state.research = [
        ResearchSource(
            title="A source",
            url="https://example.com",
            kind=ResourceKind.DOCS,
            text=SECRET_RESEARCH,
        )
    ]
    state.published = PublishedCourse(markdown_url="https://blob/course.md")
    return StoredCourse(id=COURSE, user_id=USER, job_id="j", state=state)


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    courses = FileCourseStore(tmp_path)
    monkeypatch.setattr(course_api, "course_store", courses)
    return courses


async def fetch(store) -> dict:
    await store.save(full_course())
    response = client.get(f"/courses/{COURSE}", headers=MINE)
    assert response.status_code == 200
    return response.json()


# --- what must not come back ---------------------------------------------------------


@pytest.mark.asyncio
async def test_the_fixture_really_does_contain_the_secrets(store):
    """Otherwise the two tests below pass by having nothing to find."""
    raw = full_course().model_dump_json()

    assert SECRET_ANSWER in raw and SECRET_RESEARCH in raw and "correct_index" in raw


@pytest.mark.asyncio
async def test_the_answer_key_does_not_ride_along_with_the_course(store):
    """The quiz endpoint withholds the answer and marks server-side. This endpoint was
    handing the same learner every correct_index, which made all of that decorative."""
    body = await fetch(store)

    assert "correct_index" not in str(body)
    assert SECRET_ANSWER not in str(body)


@pytest.mark.asyncio
async def test_the_workflow_state_stays_on_the_server(store):
    body = await fetch(store)

    assert SECRET_RESEARCH not in str(body)
    for internal in ("research", "review", "subject_trace", "completed_steps", "prompt"):
        assert internal not in body


@pytest.mark.asyncio
async def test_a_chapter_says_a_quiz_exists_without_carrying_it(store):
    body = await fetch(store)

    assert body["chapters"][0]["has_quiz"] is True
    assert body["chapters"][1]["has_quiz"] is False
    assert body["has_final_quiz"] is True
    assert "questions" not in str(body)


# --- what the reader needs -----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_whole_course_arrives_in_one_response(store):
    """All chapters at once, so opening one is a click rather than a request."""
    body = await fetch(store)

    assert body["title"] == "Operators"
    assert [chapter["number"] for chapter in body["chapters"]] == [1, 2]
    assert body["markdown_url"] == "https://blob/course.md"


@pytest.mark.asyncio
async def test_a_topic_arrives_as_named_blocks_not_as_markdown(store):
    body = await fetch(store)

    first = body["chapters"][0]["topics"][0]
    assert first["label"] == "1.1"
    assert first["what_it_is"] == "what it is"
    assert first["why_it_matters"] == "why it matters"
    assert first["how_to_use"] == "how to use"
    assert first["implementation"] == "print('hi')"


@pytest.mark.asyncio
async def test_a_diagram_arrives_as_its_parts(store):
    body = await fetch(store)

    diagram = body["chapters"][0]["topics"][0]["diagram"]
    assert diagram["nodes"] == ["A", "B"]
    assert diagram["edges"] == [{"source": "A", "target": "B", "label": "then"}]


@pytest.mark.asyncio
async def test_practice_is_filed_against_the_chapter_it_belongs_to(store):
    body = await fetch(store)

    assert [item["prompt"] for item in body["chapters"][0]["practice"]] == ["do it"]
    assert [item["prompt"] for item in body["chapters"][1]["practice"]] == ["recall"]


@pytest.mark.asyncio
async def test_the_projects_come_with_the_course(store):
    body = await fetch(store)

    assert body["projects"][0]["title"] == "A Project"
    assert body["projects"][0]["milestones"] == ["ship it"]


# --- courses stored before this shape existed ----------------------------------------


def test_a_chapter_with_no_topics_falls_back_to_its_markdown():
    """Courses generated before topics existed have only body_markdown. Dropping it would
    render them as empty chapters."""
    state = CourseState(job_id="j", user_id=USER, prompt="p")
    state.curriculum = Curriculum(title="Old", summary="s", chapters=[])
    state.chapters = [Chapter(number=1, title="One", body_markdown="the only content")]

    document = as_document(StoredCourse(id=COURSE, user_id=USER, job_id="j", state=state))

    assert document.chapters[0].markdown == "the only content"


def test_a_chapter_with_topics_does_not_repeat_itself_as_markdown():
    """body_markdown is the same content rendered, so shipping both doubles the payload."""
    document = as_document(full_course())

    assert document.chapters[0].markdown == ""


def test_a_course_that_never_finished_still_renders():
    """A job can fail after analysis, leaving a stored course with no curriculum."""
    state = CourseState(job_id="j", user_id=USER, prompt="p")

    document = as_document(StoredCourse(id=COURSE, user_id=USER, job_id="j", state=state))

    assert document.title == "" and document.chapters == []


# --- ownership -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_another_learners_course_is_not_found(store):
    await store.save(full_course())

    assert client.get(f"/courses/{COURSE}", headers=THEIRS).status_code == 404
