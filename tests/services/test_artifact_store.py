"""Wiring tests for the artifact store — which implementation the offline suite gets."""

from __future__ import annotations

import pytest

from backend.services.artifact_store import FileArtifactStore, artifact_store


def test_the_offline_suite_never_reaches_live_blob_storage():
    """A real BLOB_ACCOUNT_URL in .env would otherwise reroute every test at import time,
    and a published-course test would start writing to Azure."""
    assert isinstance(artifact_store, FileArtifactStore)


@pytest.mark.asyncio
async def test_a_local_course_is_written_where_the_returned_link_points(tmp_path):
    store = FileArtifactStore(tmp_path)

    url = await store.put("user-1", "job-1", "course.md", "# Local\n")

    written = tmp_path / "job-1" / "course.md"
    assert written.read_text(encoding="utf-8") == "# Local\n"
    assert url == written.as_uri()


@pytest.mark.asyncio
async def test_republishing_a_job_overwrites_rather_than_failing(tmp_path):
    store = FileArtifactStore(tmp_path)

    await store.put("user-1", "job-1", "course.md", "first")
    await store.put("user-1", "job-1", "course.md", "second")

    assert (tmp_path / "job-1" / "course.md").read_text(encoding="utf-8") == "second"


@pytest.mark.asyncio
async def test_two_jobs_do_not_land_on_top_of_each_other(tmp_path):
    store = FileArtifactStore(tmp_path)

    await store.put("user-1", "job-1", "course.md", "one")
    await store.put("user-1", "job-2", "course.md", "two")

    assert (tmp_path / "job-1" / "course.md").read_text(encoding="utf-8") == "one"
    assert (tmp_path / "job-2" / "course.md").read_text(encoding="utf-8") == "two"
