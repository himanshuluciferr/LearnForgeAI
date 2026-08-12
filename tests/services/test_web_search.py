"""Offline tests for the search router and its providers.

The service owns its HTTP client, so the transport is swapped rather than the module mocked.
"""

from __future__ import annotations

import httpx
import pytest

from backend.services import web_search
from backend.services.web_search import (
    SearchHit,
    dedupe,
    host_of,
    on_domains,
    search_github,
    search_learn,
    search_web,
    url_citations,
)


# Captured before any patching: `page_fetch.httpx` IS the httpx module, so replacing the
# attribute replaces it globally and a factory that looked it up again would call itself.
REAL_ASYNC_CLIENT = httpx.AsyncClient


def transport(handler):
    """Returns a factory that stands in for httpx.AsyncClient with a canned transport."""

    def build(**kwargs):
        kwargs.pop("transport", None)
        return REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    return build


def learn_response(titles: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": title, "url": f"https://learn.microsoft.com/{i}", "description": "d"}
                    for i, title in enumerate(titles)
                ]
            },
        )

    return handler


@pytest.mark.asyncio
async def test_learn_results_are_mapped_onto_hits(monkeypatch):
    monkeypatch.setattr(web_search.httpx, "AsyncClient", transport(learn_response(["a", "b"])))

    hits = await search_learn("anything")

    assert [hit.title for hit in hits] == ["a", "b"]
    assert {hit.provider for hit in hits} == {"learn"}


@pytest.mark.asyncio
async def test_a_result_without_a_url_is_dropped(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"title": "a", "url": ""}]})

    monkeypatch.setattr(web_search.httpx, "AsyncClient", transport(handler))

    assert await search_learn("anything") == []


@pytest.mark.asyncio
async def test_github_runs_the_scoped_pass_first(monkeypatch):
    """GitHub ranks on README text, so the canonical repository is missing from a broad
    search and only `in:name,description` surfaces it. Concatenation order is what makes
    the scoped pass win de-duplication."""
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(request.url.params["q"])
        return httpx.Response(200, json={"items": []})

    monkeypatch.setattr(web_search.httpx, "AsyncClient", transport(handler))

    await search_github("Microsoft Agent Framework")

    assert queries == [
        "Microsoft Agent Framework in:name,description",
        "Microsoft Agent Framework",
    ]


@pytest.mark.asyncio
async def test_a_rate_limited_github_narrows_the_results_instead_of_failing(monkeypatch):
    """Unauthenticated search allows about ten requests a minute and we have hit it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "rate limit exceeded"})

    monkeypatch.setattr(web_search.httpx, "AsyncClient", transport(handler))

    assert await search_github("anything") == []


def test_citations_survive_both_annotation_shapes():
    """Annotations arrive as plain dicts; reading them as attributes returns None for every
    citation and yields zero hits with no error at all."""

    class Content:
        annotations = [
            {"url": "https://a.example/x", "title": "A"},
            type("Obj", (), {"url": "https://b.example/y", "title": "B"})(),
        ]

    class Message:
        contents = [Content()]

    class Response:
        messages = [Message()]

    assert [hit.url for hit in url_citations(Response())] == [
        "https://a.example/x",
        "https://b.example/y",
    ]


def test_an_annotation_without_a_url_is_not_a_hit():
    class Content:
        annotations = [{"title": "no url here"}]

    class Message:
        contents = [Content()]

    class Response:
        messages = [Message()]

    assert url_citations(Response()) == []


def test_duplicate_urls_collapse_regardless_of_a_trailing_slash():
    hits = [
        SearchHit(title="a", url="https://x.example/docs"),
        SearchHit(title="b", url="https://x.example/docs/"),
    ]

    assert [hit.title for hit in dedupe(hits)] == ["a"]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://learn.microsoft.com/a", "learn.microsoft.com"),
        ("https://www.rust-lang.org/", "rust-lang.org"),
        ("not a url", ""),
    ],
)
def test_host_is_read_without_the_www(url, expected):
    assert host_of(url) == expected


def test_a_subdomain_counts_as_the_domain_but_a_lookalike_does_not():
    assert on_domains(SearchHit(title="t", url="https://doc.rust-lang.org/book"), ["rust-lang.org"])
    assert not on_domains(SearchHit(title="t", url="https://notrust-lang.org/"), ["rust-lang.org"])


@pytest.mark.asyncio
async def test_asking_for_a_domain_we_have_an_api_for_calls_that_api(monkeypatch):
    """Those APIs *are* the domain restriction. The hosted tool takes no parameters and
    `site:` leaked a github.com citation out of a learn.microsoft.com query."""
    called: list[str] = []

    async def fake_learn(query: str) -> list[SearchHit]:
        called.append("learn")
        return [SearchHit(title="t", url="https://learn.microsoft.com/x")]

    async def unexpected(query: str) -> list[SearchHit]:
        raise AssertionError("generic search must not run when an adapter exists")

    monkeypatch.setitem(web_search.DOMAIN_ADAPTERS, "learn.microsoft.com", fake_learn)
    monkeypatch.setattr(web_search, "search_generic", unexpected)

    hits = await search_web("x", ["learn.microsoft.com"])

    assert called == ["learn"] and len(hits) == 1


@pytest.mark.asyncio
async def test_a_domain_without_an_adapter_is_filtered_client_side(monkeypatch):
    async def fake_generic(query: str) -> list[SearchHit]:
        return [
            SearchHit(title="right", url="https://rust-lang.org/learn"),
            SearchHit(title="wrong", url="https://example.com/rust"),
        ]

    monkeypatch.setattr(web_search, "search_generic", fake_generic)

    hits = await search_web("rust", ["rust-lang.org"])

    assert [hit.title for hit in hits] == ["right"]


@pytest.mark.asyncio
async def test_an_empty_filter_is_reported_as_empty(monkeypatch):
    """Handing back unfiltered hits would silently answer a question nobody asked."""

    async def fake_generic(query: str) -> list[SearchHit]:
        return [SearchHit(title="wrong", url="https://example.com/rust")]

    monkeypatch.setattr(web_search, "search_generic", fake_generic)

    assert await search_web("rust", ["rust-lang.org"]) == []


@pytest.mark.asyncio
async def test_discovery_does_not_presume_the_subject_is_a_software_project(monkeypatch):
    """Measured: asking for "the project's own site or repository" returned the GUITAR testing
    framework for "Guitar" and Python's statistics module for "Statistics". The neutral question
    returned Britannica and Stanford, and left Python and Rust on the language."""
    asked: list[str] = []

    class FakeAgent:
        async def run(self, prompt: str):
            asked.append(prompt)
            return type("R", (), {"messages": []})()

    monkeypatch.setattr(web_search, "get_search_agent", lambda: FakeAgent())

    await web_search.search_generic("Guitar")

    question = asked[0].lower()
    assert "project" not in question and "repository" not in question


@pytest.mark.asyncio
async def test_one_failing_adapter_does_not_fail_the_search(monkeypatch):
    async def broken(query: str) -> list[SearchHit]:
        raise httpx.ConnectError("boom")

    async def working(query: str) -> list[SearchHit]:
        return [SearchHit(title="t", url="https://github.com/x")]

    monkeypatch.setitem(web_search.DOMAIN_ADAPTERS, "learn.microsoft.com", broken)
    monkeypatch.setitem(web_search.DOMAIN_ADAPTERS, "github.com", working)

    hits = await search_web("x", ["learn.microsoft.com", "github.com"])

    assert [hit.url for hit in hits] == ["https://github.com/x"]
