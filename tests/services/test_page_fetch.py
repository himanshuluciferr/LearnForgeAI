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
    body = "<p>" + " ".join(["word"] * 200) + "</p>"
    monkeypatch.setattr(
        page_fetch.httpx, "AsyncClient", transport(lambda r: httpx.Response(200, text=body))
    )

    documents = await fetch_documents([hit("https://x.example/a")], limit=5)

    assert len(documents) == 1 and documents[0].words == 200


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
