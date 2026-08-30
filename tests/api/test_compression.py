"""Tests for compressing responses.

A course document is a few hundred kilobytes of JSON read from another continent. Measured at
3.3x smaller compressed, which beats anything that could be trimmed out of it.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.api import course as course_api
from backend.api import stream as stream_api
from backend.main import app
from backend.models.course import StoredCourse
from backend.models.job import GenerationJob, JobStatus
from backend.services.course_store import FileCourseStore
from backend.services.security import create_stream_ticket
from backend.workflow.state import Chapter, CourseState, Curriculum
from tests.conftest import as_user

client = TestClient(app)

USER = "priya@contoso.com"
COURSE = "55555555-5555-4555-8555-555555555555"
MINE = as_user(USER)
GZIP = {"Accept-Encoding": "gzip"}


def wordy_course() -> StoredCourse:
    """Well past the threshold, the way a real course is."""
    state = CourseState(job_id="j", user_id=USER, prompt="p")
    state.curriculum = Curriculum(title="A Long Course", summary="s", chapters=[])
    state.chapters = [
        Chapter(number=n, title=f"Chapter {n}", body_markdown="reconcile loops. " * 400)
        for n in range(1, 6)
    ]
    return StoredCourse(id=COURSE, user_id=USER, job_id="j", state=state)


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    courses = FileCourseStore(tmp_path)
    monkeypatch.setattr(course_api, "course_store", courses)
    return courses


@pytest.mark.asyncio
async def test_a_course_is_compressed_on_the_way_out(store):
    await store.save(wordy_course())

    response = client.get(f"/courses/{COURSE}", headers={**MINE, **GZIP})

    assert response.headers.get("content-encoding") == "gzip"
    # httpx decodes it, so the body is the whole document either way.
    assert len(response.json()["chapters"]) == 5


@pytest.mark.asyncio
async def test_compressing_does_not_change_what_arrives(store):
    """The point of checking: a client must not have to know whether it was compressed."""
    await store.save(wordy_course())

    squeezed = client.get(f"/courses/{COURSE}", headers={**MINE, **GZIP})
    plain = client.get(f"/courses/{COURSE}", headers={**MINE, "Accept-Encoding": "identity"})

    assert plain.headers.get("content-encoding") is None
    assert squeezed.json() == plain.json()


def test_a_small_reply_is_left_alone():
    """Compressing a handful of bytes costs more than it saves."""
    response = client.get("/health", headers=GZIP)

    assert response.headers.get("content-encoding") is None


class OneJob:
    def __init__(self, job: GenerationJob) -> None:
        self._job = job

    async def get(self, job_id: str, user_id: str | None = None) -> GenerationJob | None:
        return self._job


def test_the_stream_is_not_compressed(monkeypatch):
    """A compressor holds small frames back until it has enough to emit, which is exactly the
    delay a stream exists to avoid. The response declares its own encoding to opt out."""
    monkeypatch.setattr(
        stream_api,
        "job_store",
        OneJob(
            GenerationJob(
                id="j1", user_id=USER, prompt="p", status=JobStatus.COMPLETED, percent=100
            )
        ),
    )

    response = client.get(
        "/courses/j1/stream",
        params={"ticket": create_stream_ticket(USER)},
        headers=GZIP,
    )

    assert response.headers.get("content-encoding") == "identity"
    assert response.text.startswith("event: done")
    assert json.loads(response.text.split("data: ", 1)[1])["status"] == "completed"
