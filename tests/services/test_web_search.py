"""Offline tests for the web search service: parsing, merging, and surviving an outage."""

import httpx
import pytest

from backend.services import web_search
from backend.services.web_search import SearchHit, search_github, search_learn, search_web
from backend.workflow.state import ResourceKind

LEARN_BODY = {
    "results": [
        {
            "title": "Agent Framework documentation",
            "url": "https://learn.microsoft.com/en-us/agent-framework/",
            "description": "Build agents.",
        },
        {"title": "No url here", "description": "dropped"},
    ]
}
GITHUB_BODY = {
    "items": [
        {
            "full_name": "microsoft/agent-framework",
            "html_url": "https://github.com/microsoft/agent-framework",
            "description": "The framework.",
        }
    ]
}


def serving(body, status: int = 200):
    return httpx.MockTransport(lambda request: httpx.Response(status, json=body))


@pytest.mark.asyncio
async def test_learn_results_become_hits():
    async with httpx.AsyncClient(transport=serving(LEARN_BODY)) as client:
        hits = await search_learn(client, "Microsoft Agent Framework", 5)

    assert [hit.url for hit in hits] == ["https://learn.microsoft.com/en-us/agent-framework/"]
    assert hits[0].title == "Agent Framework documentation"
    assert hits[0].kind == ResourceKind.MICROSOFT_LEARN


@pytest.mark.asyncio
async def test_a_result_with_no_url_is_dropped_rather_than_guessed():
    async with httpx.AsyncClient(transport=serving(LEARN_BODY)) as client:
        assert len(await search_learn(client, "q", 5)) == 1


@pytest.mark.asyncio
async def test_a_repository_with_no_description_still_becomes_a_hit():
    body = {"items": [{"full_name": "a/b", "html_url": "https://github.com/a/b"}]}

    async with httpx.AsyncClient(transport=serving(body)) as client:
        hits = await search_github(client, "q", 5)

    assert hits[0].snippet == ""
    assert hits[0].kind == ResourceKind.GITHUB


@pytest.mark.asyncio
async def test_github_is_asked_for_the_project_before_projects_that_use_it():
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.params["q"])
        return httpx.Response(200, json={"items": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await search_github(client, "Microsoft Agent Framework", 5)

    # The scoped query comes first, so its results win the de-duplication in search_web.
    assert asked == [
        "Microsoft Agent Framework in:name,description",
        "Microsoft Agent Framework",
    ]


@pytest.mark.asyncio
async def test_the_query_is_sent_to_each_provider(monkeypatch):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.host] = request.url.params.get("search") or request.url.params.get("q")
        body = LEARN_BODY if "learn" in request.url.host else GITHUB_BODY
        return httpx.Response(200, json=body)

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    await search_web("Microsoft Agent Framework")

    assert seen == {
        "learn.microsoft.com": "Microsoft Agent Framework",
        "api.github.com": "Microsoft Agent Framework",
    }


@pytest.mark.asyncio
async def test_results_from_every_provider_are_merged(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        body = LEARN_BODY if "learn" in request.url.host else GITHUB_BODY
        return httpx.Response(200, json=body)

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    hits = await search_web("q")

    assert {hit.kind for hit in hits} == {ResourceKind.MICROSOFT_LEARN, ResourceKind.GITHUB}


@pytest.mark.asyncio
async def test_one_provider_failing_narrows_the_results_instead_of_failing_the_job(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "learn" in request.url.host:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json=GITHUB_BODY)

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    hits = await search_web("q")

    assert [hit.kind for hit in hits] == [ResourceKind.GITHUB]


@pytest.mark.asyncio
async def test_every_provider_failing_returns_nothing_rather_than_raising(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    assert await search_web("q") == []


@pytest.mark.asyncio
async def test_a_page_found_by_two_providers_is_only_listed_once(monkeypatch):
    shared = {
        "results": [{"title": "t", "url": "https://example.com/x", "description": "d"}],
        "items": [{"full_name": "t", "html_url": "https://example.com/x", "description": "d"}],
    }

    _patch_client(monkeypatch, serving(shared))

    assert [hit.url for hit in await search_web("q")] == ["https://example.com/x"]


def _patch_client(monkeypatch, transport):
    """Makes the service's own client speak to a mock instead of the internet."""
    original = httpx.AsyncClient

    def build(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(web_search.httpx, "AsyncClient", build)


def test_a_hit_is_just_data():
    hit = SearchHit(title="t", url="https://a.dev", snippet="s", kind=ResourceKind.DOCS)

    assert hit.url == "https://a.dev"
