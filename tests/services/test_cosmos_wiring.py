"""Offline tests for the Cosmos swap: document mapping, store selection, partition scoping.

Nothing here talks to Azure — the point is that the seams behave the same either way.
"""

import json

import pytest

from backend.models.course import StoredCourse
from backend.models.job import GenerationJob, JobStatus
from backend.services import cosmos as cosmos_module
from backend.services.cosmos import to_document
from backend.services.course_store import FileCourseStore, course_store
from backend.services.job_store import InMemoryJobStore, job_store
from backend.workflow.state import CourseState, WorkflowStep


def make_job(**overrides) -> GenerationJob:
    fields = {"id": "job-1", "user_id": "u1", "prompt": "teach me rust"}
    return GenerationJob(**(fields | overrides))


def make_course(course_id: str = "c1", user_id: str = "u1") -> StoredCourse:
    return StoredCourse(
        id=course_id,
        user_id=user_id,
        job_id="job-1",
        state=CourseState(job_id="job-1", user_id=user_id, prompt="teach me rust"),
    )


def test_the_offline_suite_never_reaches_live_cosmos():
    """A real COSMOS_ENDPOINT in .env would otherwise reroute every test at import time."""
    assert isinstance(job_store, InMemoryJobStore)
    assert isinstance(course_store, FileCourseStore)


def test_documents_contain_only_json_primitives():
    """Cosmos stores JSON, so datetimes and StrEnums must already be converted."""
    document = to_document(make_job(status=JobStatus.RUNNING, step=WorkflowStep.RESEARCH))

    json.dumps(document)  # raises TypeError if a datetime or enum survived
    assert document["status"] == "running"
    assert document["step"] == "research"
    assert isinstance(document["created_at"], str)


def test_a_stored_course_survives_the_round_trip():
    course = make_course()

    restored = StoredCourse.model_validate(to_document(course))

    assert restored == course


def test_cosmos_metadata_fields_are_ignored_on_read():
    """Cosmos decorates every document; the models must not trip over the extra keys."""
    document = to_document(make_job()) | {"_rid": "x", "_etag": "y", "_ts": 1, "_self": "z"}

    assert GenerationJob.model_validate(document).id == "job-1"


def test_local_stores_are_used_when_no_endpoint_is_configured(monkeypatch):
    monkeypatch.setattr(cosmos_module, "get_settings", lambda: type("S", (), {"cosmos_endpoint": ""}))
    assert cosmos_module.cosmos_enabled() is False


def test_cosmos_is_used_once_an_endpoint_is_configured(monkeypatch):
    settings = type("S", (), {"cosmos_endpoint": "https://example.documents.azure.com:443/"})
    monkeypatch.setattr(cosmos_module, "get_settings", lambda: settings)
    assert cosmos_module.cosmos_enabled() is True


@pytest.mark.asyncio
async def test_a_job_is_not_returned_to_the_wrong_user():
    store = InMemoryJobStore()
    await store.create(make_job())

    assert await store.get("job-1", "u1") is not None
    assert await store.get("job-1", "someone-else") is None


@pytest.mark.asyncio
async def test_a_job_read_without_a_user_still_works():
    """The progress route allows an anonymous read today; Cosmos answers it with a query."""
    store = InMemoryJobStore()
    await store.create(make_job())

    assert await store.get("job-1") is not None


@pytest.mark.asyncio
async def test_a_job_update_for_the_wrong_user_changes_nothing():
    store = InMemoryJobStore()
    await store.create(make_job())

    assert await store.update("job-1", user_id="someone-else", percent=90) is None
    assert (await store.get("job-1")).percent == 0


@pytest.mark.asyncio
async def test_a_course_is_not_returned_to_the_wrong_user(tmp_path):
    store = FileCourseStore(tmp_path)
    course = make_course(course_id="11111111-1111-1111-1111-111111111111")
    await store.save(course)

    assert await store.get(course.id, "u1") is not None
    assert await store.get(course.id, "someone-else") is None
