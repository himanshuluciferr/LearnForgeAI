"""Live tests for Blob Storage. Opt in with `pytest -m live`. No model calls.

The thing worth testing here is not that an upload succeeds — it is that the link we hand
a learner works and that the one without a token does not. Both were wrong once: the
delegation key window ran a minute past Azure's seven-day cap, which Azure reports as
"InvalidXmlNodeValue" rather than as an expiry problem.
"""

from __future__ import annotations

import os

import httpx
import pytest
import pytest_asyncio

from backend.config.settings import get_settings
from backend.services import blob_storage
from backend.services.blob_storage import blob_path, close_blob_storage, read_link, upload

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]

CONTENT = "# Live\n\nwritten by test_blob_storage_live\n"
MARKDOWN = "text/markdown; charset=utf-8"


@pytest.fixture(scope="module", autouse=True)
def real_storage_account():
    """conftest blanks BLOB_ACCOUNT_URL so the offline suite can never reach Azure. These
    tests are the one place that has to opt back in, so they undo it explicitly rather
    than skipping in silence — a live test that quietly skips is worse than no test.

    Settings are lru_cached and the env var outranks .env, so both have to be reset.
    """
    blanked = os.environ.pop("BLOB_ACCOUNT_URL", None)
    get_settings.cache_clear()
    blob_storage._connection.cache_clear()

    if not blob_storage.blob_enabled():
        pytest.skip("BLOB_ACCOUNT_URL is not set in .env")

    yield

    if blanked is not None:
        os.environ["BLOB_ACCOUNT_URL"] = blanked
    get_settings.cache_clear()


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def link(real_storage_account) -> str:
    url = await upload(blob_path("live-user", "live-job", "course.md"), CONTENT, MARKDOWN)
    yield url
    await close_blob_storage()


async def test_a_signed_link_returns_exactly_what_was_uploaded(link):
    async with httpx.AsyncClient() as client:
        response = await client.get(link)

    assert response.status_code == 200
    assert response.text == CONTENT


async def test_the_same_url_without_its_token_is_refused(link):
    """The course names the learner's own systems, so a guessable URL must give nothing."""
    async with httpx.AsyncClient() as client:
        response = await client.get(link.split("?")[0])

    assert response.status_code in (403, 404, 409)


async def test_the_link_carries_read_only_permission(link):
    assert "sp=r&" in f"{link}&" or link.endswith("sp=r")


async def test_republishing_a_job_replaces_the_course_rather_than_failing(link):
    """A rewrite reuses the job id, so the second upload must overwrite the first."""
    path = blob_path("live-user", "live-job", "course.md")
    second = await upload(path, "# Second\n", MARKDOWN)

    async with httpx.AsyncClient() as client:
        response = await client.get(second)

    assert response.text == "# Second\n"


async def test_a_link_can_be_reissued_without_uploading_again(link):
    fresh = await read_link(blob_path("live-user", "live-job", "course.md"))

    async with httpx.AsyncClient() as client:
        response = await client.get(fresh)

    assert response.status_code == 200
