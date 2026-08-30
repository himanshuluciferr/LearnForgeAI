"""Tests for closing out runs the server did not survive.

A run lives in a BackgroundTask, which dies with the process. Its job row does not, so a
learner is left watching a bar that will never move. Two real jobs were found sitting at 30%
and 60% after a restart, which is what these tests describe.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.models.job import GenerationJob, JobStatus
from backend.services.job_store import ABANDONED, InMemoryJobStore
from backend.workflow.state import WorkflowStep


def job(status: JobStatus, percent: int = 0) -> GenerationJob:
    return GenerationJob(
        id=f"job-{status}-{percent}",
        user_id="u1",
        prompt="teach me rust",
        status=status,
        percent=percent,
        step=WorkflowStep.CHAPTER,
    )


async def stored(*jobs: GenerationJob) -> InMemoryJobStore:
    store = InMemoryJobStore()
    for one in jobs:
        await store.create(one)
    return store


@pytest.mark.asyncio
async def test_a_run_that_was_still_going_is_marked_failed():
    store = await stored(job(JobStatus.RUNNING, 60))

    assert await store.abandon_unfinished() == 1

    left = await store.get("job-running-60", "u1")
    assert left is not None and left.status is JobStatus.FAILED


@pytest.mark.asyncio
async def test_a_queued_run_counts_too():
    """It never started, so nothing will ever pick it up."""
    store = await stored(job(JobStatus.QUEUED))

    assert await store.abandon_unfinished() == 1


@pytest.mark.asyncio
async def test_the_learner_is_told_why():
    store = await stored(job(JobStatus.RUNNING, 30))

    await store.abandon_unfinished()

    left = await store.get("job-running-30", "u1")
    assert left is not None and left.error == ABANDONED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.REJECTED,
        JobStatus.NEEDS_CHOICE,
        JobStatus.NEEDS_CONFIRMATION,
    ],
)
async def test_a_run_that_had_already_settled_is_left_alone(status):
    """needs-choice and needs-confirmation matter most here: they are waiting on the learner,
    not on a worker, and failing them would throw away a run that is still answerable."""
    store = await stored(job(status))

    assert await store.abandon_unfinished() == 0

    left = await store.get(f"job-{status}-0", "u1")
    assert left is not None and left.status is status


@pytest.mark.asyncio
async def test_a_finished_course_is_not_disturbed_by_a_restart():
    store = await stored(job(JobStatus.COMPLETED, 100), job(JobStatus.RUNNING, 60))

    assert await store.abandon_unfinished() == 1


@pytest.mark.asyncio
async def test_nothing_to_do_is_not_an_error():
    assert await InMemoryJobStore().abandon_unfinished() == 0


def test_startup_closes_them_out(monkeypatch):
    """Wired into the lifespan, so it happens without anyone remembering to run it."""
    swept: list[bool] = []

    class Watching(InMemoryJobStore):
        async def abandon_unfinished(self) -> int:
            swept.append(True)
            return 0

    monkeypatch.setattr(main, "job_store", Watching())

    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200

    assert swept == [True]
