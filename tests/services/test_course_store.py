"""Tests for course persistence, including the id guard on the retrieval path."""

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.models.course import StoredCourse
from backend.schemas.document import as_document
from backend.services.course_store import FileCourseStore, as_stored, load, repair
from backend.workflow.state import (
    Chapter,
    CourseState,
    Curriculum,
    LearningRequest,
    Quiz,
    QuizQuestion,
    WorkflowStep,
)


def make_course(user_id: str = "u1") -> StoredCourse:
    state = CourseState(job_id="j1", user_id=user_id, prompt="Teach me Rust")
    state.request = LearningRequest(is_learning_request=True, skill="Rust")
    state.mark(WorkflowStep.REQUIREMENT)
    return StoredCourse(id=str(uuid4()), user_id=user_id, job_id="j1", state=state)


@pytest.mark.asyncio
async def test_save_then_get_round_trips_every_agent_field(tmp_path):
    store = FileCourseStore(tmp_path)
    course = make_course()

    await store.save(course)
    loaded = await store.get(course.id)

    assert loaded is not None
    assert loaded.state.request.skill == "Rust"
    assert loaded.state.completed_steps == [WorkflowStep.REQUIREMENT]


@pytest.mark.asyncio
async def test_save_creates_the_directory_if_missing(tmp_path):
    store = FileCourseStore(tmp_path / "does" / "not" / "exist")
    course = make_course()

    await store.save(course)

    assert await store.get(course.id) is not None


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown_id(tmp_path):
    assert await FileCourseStore(tmp_path).get(str(uuid4())) is None


@pytest.mark.parametrize(
    "course_id",
    ["../../.env", "..\\..\\.env", "not-a-uuid", "", "a/b"],
    ids=["posix-traversal", "windows-traversal", "plain-string", "empty", "slash"],
)
@pytest.mark.asyncio
async def test_non_uuid_ids_are_refused_before_touching_disk(tmp_path, course_id):
    assert await FileCourseStore(tmp_path).get(course_id) is None

# --- courses written by earlier versions ---------------------------------------------


def legacy_document() -> dict:
    """The two shapes actually found in the live container: `research[].text` from when it
    was optional, and a step name that has since been retired."""
    document = json.loads(make_course().model_dump_json())
    document["state"]["research"] = [
        {"title": "A source", "url": "https://example.com", "kind": "docs", "rank_score": 1}
    ]
    document["state"]["completed_steps"] = ["requirement", "skill-analysis"]
    return document


def test_a_legacy_course_would_not_validate_as_it_stands():
    """Otherwise the repair below is tested against nothing."""
    with pytest.raises(ValidationError):
        StoredCourse.model_validate(legacy_document())


def test_a_legacy_course_is_repaired_rather_than_refused():
    course = load(legacy_document())

    assert course is not None
    assert course.state.research[0].text == ""
    assert course.state.completed_steps == [WorkflowStep.REQUIREMENT]


def test_a_course_that_cannot_be_repaired_is_skipped_not_raised():
    """One bad row must not fail a whole library listing."""
    assert load({"id": "x", "state": {"job_id": "j"}}) is None


def test_repair_leaves_a_current_course_alone():
    document = json.loads(make_course().model_dump_json())
    before = json.dumps(document, sort_keys=True)

    repair(document)

    assert json.dumps(document, sort_keys=True) == before


@pytest.mark.asyncio
async def test_a_listing_skips_an_unreadable_course_and_keeps_the_rest(tmp_path):
    store = FileCourseStore(tmp_path)
    await store.save(make_course("u1"))
    (tmp_path / "broken.json").write_text('{"id": "broken", "user_id": "u1"}', encoding="utf-8")
    (tmp_path / "notjson.json").write_text("{{{", encoding="utf-8")

    assert len(await store.for_user("u1")) == 1

# --- the projected read used for display ----------------------------------------------


def projected_row(course: StoredCourse, quiz_chapters: list, quiz_count: int) -> dict:
    """What the display query returns: the reader's fields, and quizzes as numbers only."""
    document = json.loads(course.model_dump_json())
    state = document["state"]
    return {
        "id": document["id"],
        "user_id": document["user_id"],
        "job_id": document["job_id"],
        "created_at": document["created_at"],
        "curriculum": state["curriculum"],
        "chapters": state["chapters"],
        "practice": state["practice"],
        "projects": state["projects"],
        "published": state["published"],
        "quiz_chapters": quiz_chapters,
        "quiz_count": quiz_count,
    }


def course_with_quizzes() -> StoredCourse:
    course = make_course()
    course.state.curriculum = Curriculum(title="A Course", summary="s", chapters=[])
    course.state.chapters = [Chapter(number=1, title="One", body_markdown="body")]
    course.state.quizzes = [
        Quiz(scope="Chapter 1", chapter_number=1, questions=[]),
        Quiz(scope="Final", chapter_number=None, questions=[]),
    ]
    return course


def test_a_projected_row_reads_back_as_the_same_document():
    """The display read must not become a second, drifting definition of a course."""
    course = course_with_quizzes()

    rebuilt = load(as_stored(projected_row(course, [1], 2)))

    assert rebuilt is not None
    assert as_document(rebuilt).model_dump_json() == as_document(course).model_dump_json()


def test_the_final_quiz_survives_cosmos_dropping_its_null():
    """A final quiz has no chapter number, and the array projection omits it rather than
    returning null, so it is restored from the count."""
    rebuilt = load(as_stored(projected_row(course_with_quizzes(), [1], 2)))

    assert rebuilt is not None
    assert as_document(rebuilt).has_final_quiz is True


def test_a_course_with_no_final_quiz_does_not_grow_one():
    course = course_with_quizzes()
    course.state.quizzes = [Quiz(scope="Chapter 1", chapter_number=1, questions=[])]

    rebuilt = load(as_stored(projected_row(course, [1], 1)))

    assert rebuilt is not None
    assert as_document(rebuilt).has_final_quiz is False


def test_the_projection_carries_no_quiz_questions():
    """The whole reason the display read exists: the answer key is 15% of the bytes and the
    page must never receive it."""
    course = course_with_quizzes()
    course.state.quizzes[0].questions = [
        QuizQuestion(question="q", options=["a", "b"], correct_index=1, explanation="e")
    ]

    rebuilt = load(as_stored(projected_row(course, [1], 2)))

    assert rebuilt is not None
    assert all(quiz.questions == [] for quiz in rebuilt.state.quizzes)


def test_a_course_that_never_finished_projects_without_raising():
    """Nulls everywhere: a job can fail before a curriculum exists."""
    row = {
        "id": "c1",
        "user_id": "u1",
        "job_id": "j1",
        "created_at": "2026-01-01T00:00:00Z",
        "quiz_count": 0,
    }

    rebuilt = load(as_stored(row))

    assert rebuilt is not None and rebuilt.state.chapters == []


@pytest.mark.asyncio
async def test_the_file_store_summarises_the_same_way_the_query_does(tmp_path):
    store = FileCourseStore(tmp_path)
    await store.save(course_with_quizzes())

    rows = await store.summaries("u1")

    assert rows[0]["title"] == "A Course" and rows[0]["chapters"] == 1
