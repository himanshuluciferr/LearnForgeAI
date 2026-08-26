"""Tests for the watcher that keeps a progress card current.

The point of it: a learner should not press "Check again" to watch a twenty-minute build. The
card already on screen is edited in place, so the conversation does not fill with stale bars.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from teams_bot.backend_client import BackendClient
from teams_bot.watcher import JobWatcher

USER = "aad-1"


class StubAdapter:
    """Records the activities a continued conversation tried to update."""

    def __init__(self) -> None:
        self.updated: list[object] = []

    async def continue_conversation(self, reference, callback, bot_id=None):
        await callback(SimpleNamespace(update_activity=self._capture))

    async def _capture(self, activity) -> None:
        self.updated.append(activity)


def reference() -> SimpleNamespace:
    return SimpleNamespace(bot=SimpleNamespace(id="bot"))


def client_returning(*jobs: dict) -> BackendClient:
    """Answers each poll with the next job state, then repeats the last."""
    remaining = list(jobs)

    def handler(request: httpx.Request) -> httpx.Response:
        job = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return httpx.Response(200, json=job)

    return BackendClient(
        base_url="http://backend", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


def running(percent: int) -> dict:
    return {"job_id": "j1", "status": "running", "percent": percent, "step": "chapter"}


def cards(adapter: StubAdapter) -> str:
    return json.dumps(
        [
            attachment.content
            for activity in adapter.updated
            for attachment in (getattr(activity, "attachments", None) or [])
        ]
    )


@pytest.mark.asyncio
async def test_the_card_is_edited_rather_than_a_new_one_posted():
    """Without the id, every update would land as another card and the chat would fill with
    stale progress bars."""
    adapter = StubAdapter()
    watcher = JobWatcher(adapter, client_returning(running(30), {"job_id": "j1", "status": "failed"}), 0)

    await watcher.watch(reference(), "activity-7", "j1", USER)

    assert all(activity.id == "activity-7" for activity in adapter.updated)


@pytest.mark.asyncio
async def test_it_stops_once_the_run_stops():
    adapter = StubAdapter()
    watcher = JobWatcher(
        adapter, client_returning(running(30), {"job_id": "j1", "status": "rejected", "detail": "no"}), 0
    )

    await watcher.watch(reference(), "a", "j1", USER)

    assert len(adapter.updated) == 2


@pytest.mark.asyncio
async def test_an_unchanged_job_is_not_redrawn():
    """An edit that changes nothing still makes the client re-render, and Teams rate-limits
    updates."""
    adapter = StubAdapter()
    watcher = JobWatcher(
        adapter,
        client_returning(running(30), running(30), running(30), {"job_id": "j1", "status": "failed"}),
        0,
    )

    await watcher.watch(reference(), "a", "j1", USER)

    assert len(adapter.updated) == 2


@pytest.mark.asyncio
async def test_the_same_job_is_only_watched_once():
    """Pressing Check again while a watcher runs must not start a second one racing it."""
    adapter = StubAdapter()
    watcher = JobWatcher(adapter, client_returning(running(10)), 0.01)

    first = watcher.watch(reference(), "a", "j1", USER)
    second = watcher.watch(reference(), "a", "j1", USER)

    assert second is None
    first.cancel()


@pytest.mark.asyncio
async def test_a_card_with_no_activity_id_is_not_watched():
    """Nothing to edit, so a watcher would post duplicates instead."""
    assert JobWatcher(StubAdapter(), client_returning(running(10)), 0).watch(
        reference(), "", "j1", USER
    ) is None


@pytest.mark.asyncio
async def test_a_finished_run_is_shown_as_the_finished_course():
    adapter = StubAdapter()
    finished = {"job_id": "j1", "status": "completed", "course_id": "c1"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/progress/c1":
            return httpx.Response(
                200,
                json={
                    "course_id": "c1",
                    "title": "Operators",
                    "percent": 0,
                    "next_chapter": 1,
                    "markdown_url": "https://blob/course.md?sig=x",
                    "chapters": [
                        {"number": 1, "title": "One", "read": False, "best_quiz_percent": None}
                    ],
                },
            )
        return httpx.Response(200, json=finished)

    client = BackendClient(
        base_url="http://backend", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    await JobWatcher(adapter, client, 0).watch(reference(), "a", "j1", USER)

    assert "Operators" in cards(adapter)


@pytest.mark.asyncio
async def test_a_backend_that_falls_over_stops_the_watcher_quietly():
    """The learner still has a card with a working button; a crash loop would not help them."""
    adapter = StubAdapter()
    client = BackendClient(
        base_url="http://backend",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )

    await JobWatcher(adapter, client, 0).watch(reference(), "a", "j1", USER)

    assert adapter.updated == []
