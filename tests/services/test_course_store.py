"""Tests for course persistence, including the id guard on the retrieval path."""

from uuid import uuid4

import pytest

from backend.models.course import StoredCourse
from backend.services.course_store import FileCourseStore
from backend.workflow.state import CourseState, LearningRequest, WorkflowStep


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
