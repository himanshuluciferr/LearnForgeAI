"""Offline tests for page fetching: the SSRF boundary, extraction and the word floor."""

from __future__ import annotations

import httpx
import pytest

from backend.services import page_fetch
from backend.services.page_fetch import (
    MIN_WORDS,
    USER_AGENT,
    extract_text,
    fetch_documents,
    is_fetchable,
)
from backend.services.web_search import SearchHit


REAL_ASYNC_CLIENT = httpx.AsyncClient


def transport(handler):
    def build(**kwargs):
        kwargs.pop("transport", None)
        return REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    return build


def hit(url: str) -> SearchHit:
    return SearchHit(title="t", url=url)


@pytest.mark.parametrize(
    "url",
    [
        # The address DefaultAzureCredential itself probes, so we know it is reachable inside.
        "https://169.254.169.254/metadata/identity",
        "https://127.0.0.1/admin",
        "https://10.0.0.5/internal",
        "https://[::1]/x",
        "https://build.internal/x",
        "https://service.local/x",
        "https://localhost/x",
        "http://learn.microsoft.com/agent-framework/",
        "ftp://learn.microsoft.com/x",
        "not a url",
    ],
)
def test_private_and_non_https_targets_are_refused(url):
    assert not is_fetchable(url)


@pytest.mark.parametrize(
    "url",
    ["https://learn.microsoft.com/agent-framework/", "https://github.com/microsoft/agent-framework"],
)
def test_public_https_pages_are_fetchable(url):
    assert is_fetchable(url)


def test_script_and_nav_go_with_their_contents():
    """Stripping tags alone leaves the words that were inside them."""
    html = "<nav>menu junk</nav><p>real words</p><script>var hidden = 1;</script>"

    assert extract_text(html) == "real words"


def test_entities_and_runs_of_whitespace_collapse():
    assert extract_text("<p>a &amp;   b\n\nc</p>") == "a b c"


@pytest.mark.asyncio
async def test_a_page_is_read_and_kept_as_text(monkeypatch):
    body = "<p>" + " ".join(["word"] * (MIN_WORDS * 2)) + "</p>"
    monkeypatch.setattr(
        page_fetch.httpx, "AsyncClient", transport(lambda r: httpx.Response(200, text=body))
    )

    documents = await fetch_documents([hit("https://x.example/a")], limit=5)

    assert len(documents) == 1 and documents[0].words == MIN_WORDS * 2


@pytest.mark.asyncio
async def test_a_page_that_is_mostly_navigation_is_dropped(monkeypatch):
    """A landing page yielded 136 words of menu; below the floor it is not evidence."""
    body = "<p>" + " ".join(["word"] * (MIN_WORDS - 1)) + "</p>"
    monkeypatch.setattr(
        page_fetch.httpx, "AsyncClient", transport(lambda r: httpx.Response(200, text=body))
    )

    assert await fetch_documents([hit("https://x.example/a")], limit=5) == []


@pytest.mark.asyncio
async def test_a_403_narrows_the_evidence_rather_than_failing_the_run(monkeypatch):
    monkeypatch.setattr(
        page_fetch.httpx, "AsyncClient", transport(lambda r: httpx.Response(403, text="no"))
    )

    assert await fetch_documents([hit("https://x.example/a")], limit=5) == []


@pytest.mark.asyncio
async def test_a_connection_error_is_not_allowed_to_escape(monkeypatch):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns")

    monkeypatch.setattr(page_fetch.httpx, "AsyncClient", transport(boom))

    assert await fetch_documents([hit("https://x.example/a")], limit=5) == []


@pytest.mark.asyncio
async def test_blocked_urls_never_reach_the_network(monkeypatch):
    def unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"must not fetch {request.url}")

    monkeypatch.setattr(page_fetch.httpx, "AsyncClient", transport(unexpected))

    assert await fetch_documents([hit("https://169.254.169.254/x")], limit=5) == []


@pytest.mark.asyncio
async def test_the_limit_caps_outbound_requests(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="<p>" + " ".join(["w"] * 200) + "</p>")

    monkeypatch.setattr(page_fetch.httpx, "AsyncClient", transport(handler))

    await fetch_documents([hit(f"https://x.example/{n}") for n in range(9)], limit=2)

    assert len(seen) == 2


def test_the_user_agent_carries_a_contact_url():
    """Wikimedia 403s a vague contact string on both its API and REST endpoints, and returns
    200 as soon as the User-Agent names somewhere to complain to."""
    assert "https://" in USER_AGENT


TREE_URL = "https://github.com/microsoft/agent-framework/tree/main/python/samples/02-agents"
LISTING = [
    {"type": "file", "name": "chat.py", "download_url": "https://raw.githubusercontent.com/a.py"},
    {"type": "file", "name": "logo.png", "download_url": "https://raw.githubusercontent.com/b.png"},
    {"type": "dir", "name": "nested", "download_url": None},
]


@pytest.mark.asyncio
async def test_a_github_folder_is_read_as_code_not_as_a_list_of_file_names(monkeypatch):
    """A measured failure: two sample folders yielded 515 and 588 words of directory listing,
    and the chapters then invented the API that was sitting in the files."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(200, json=LISTING)
        return httpx.Response(200, text="from agent_framework import Agent\n" * 200)

    monkeypatch.setattr(page_fetch.httpx, "AsyncClient", transport(handler))

    documents = await fetch_documents([hit(TREE_URL)], limit=5)

    assert len(documents) == 1
    assert "from agent_framework import Agent" in documents[0].text
    # The URL the learner is cited stays the folder they can browse, not the raw file.
    assert documents[0].url == TREE_URL


@pytest.mark.asyncio
async def test_only_source_files_in_a_folder_are_downloaded(monkeypatch):
    downloaded: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(200, json=LISTING)
        downloaded.append(str(request.url))
        return httpx.Response(200, text="x " * 200)

    monkeypatch.setattr(page_fetch.httpx, "AsyncClient", transport(handler))

    await fetch_documents([hit(TREE_URL)], limit=5)

    assert downloaded == ["https://raw.githubusercontent.com/a.py"]


@pytest.mark.asyncio
async def test_a_download_url_from_the_api_still_passes_the_ssrf_boundary(monkeypatch):
    """`download_url` is supplied by GitHub, which makes it input to our fetcher, not a fact."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(
                200,
                json=[
                    {
                        "type": "file",
                        "name": "evil.py",
                        "download_url": "https://169.254.169.254/metadata",
                    }
                ],
            )
        raise AssertionError(f"fetched a blocked address: {request.url}")

    monkeypatch.setattr(page_fetch.httpx, "AsyncClient", transport(handler))

    assert await fetch_documents([hit(TREE_URL)], limit=5) == []


@pytest.mark.asyncio
async def test_a_github_file_is_read_raw_rather_than_stripped_of_its_syntax(monkeypatch):
    code = "def build() -> Workflow:\n    return WorkflowBuilder(start_executor=first).build()\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "raw.githubusercontent.com"
        return httpx.Response(200, text=code * 100)

    monkeypatch.setattr(page_fetch.httpx, "AsyncClient", transport(handler))

    documents = await fetch_documents(
        [hit("https://github.com/microsoft/agent-framework/blob/main/python/x.py")], limit=5
    )

    # extract_text would have eaten `-> Workflow` as if it were a tag.
    assert "-> Workflow" in documents[0].text


@pytest.mark.asyncio
async def test_a_folder_whose_listing_fails_narrows_the_evidence(monkeypatch):
    monkeypatch.setattr(
        page_fetch.httpx, "AsyncClient", transport(lambda r: httpx.Response(403, text="rate limit"))
    )

    assert await fetch_documents([hit(TREE_URL)], limit=5) == []
