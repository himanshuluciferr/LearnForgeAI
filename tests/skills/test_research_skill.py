"""Offline tests for the research and ranking skills: URL safety, liveness, ordering."""

import httpx
import pytest

from backend.skills.ranking.skill import rank_sources
from backend.skills.research.skill import is_fetchable, is_reachable, verify_sources
from backend.workflow.state import ResearchSource, ResourceKind


def make_source(url: str = "https://learn.microsoft.com/azure/search/", kind=ResourceKind.DOCS):
    return ResearchSource(title="t", url=url, kind=kind, summary="s")


@pytest.mark.parametrize(
    "url",
    [
        "https://learn.microsoft.com/azure/search/",
        "https://github.com/Azure/azure-sdk-for-python",
    ],
)
def test_public_https_urls_are_fetchable(url):
    assert is_fetchable(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://learn.microsoft.com/azure/search/",  # plaintext
        "file:///c:/LearnForgeAI/.env",  # local file read
        "https://localhost/admin",
        "https://127.0.0.1/admin",
        "https://10.0.0.5/internal",
        "https://192.168.1.1/router",
        "https://169.254.169.254/latest/meta-data/",  # cloud metadata service
        "https://vault.internal/secrets",
        "https://",
    ],
)
def test_unsafe_urls_are_refused(url):
    assert is_fetchable(url) is False


@pytest.mark.asyncio
async def test_verify_sources_never_calls_out_for_unsafe_urls():
    # No transport is mocked, so a real request here would fail the test rather than pass it.
    assert await verify_sources([make_source("https://169.254.169.254/latest/")]) == []


@pytest.mark.asyncio
async def test_verify_sources_handles_an_empty_list():
    assert await verify_sources([]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [(200, True), (301, True), (404, False), (410, False), (500, False)],
)
async def test_reachability_follows_the_status_code(status, expected):
    transport = httpx.MockTransport(lambda request: httpx.Response(status))
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        assert await is_reachable(client, make_source()) is expected


@pytest.mark.asyncio
async def test_a_server_that_refuses_head_is_retried_with_get():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(405 if request.method == "HEAD" else 200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await is_reachable(client, make_source()) is True

    assert seen == ["HEAD", "GET"]


@pytest.mark.asyncio
async def test_a_network_error_drops_the_source_instead_of_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await is_reachable(client, make_source()) is False


def test_ranking_puts_primary_sources_first():
    sources = [
        make_source(kind=ResourceKind.VIDEO),
        make_source(kind=ResourceKind.BLOG),
        make_source(kind=ResourceKind.DOCS),
        make_source(kind=ResourceKind.GITHUB),
        make_source(kind=ResourceKind.MICROSOFT_LEARN),
    ]

    kinds = [source.kind for source in rank_sources(sources)]

    assert kinds == [
        ResourceKind.DOCS,
        ResourceKind.MICROSOFT_LEARN,
        ResourceKind.GITHUB,
        ResourceKind.BLOG,
        ResourceKind.VIDEO,
    ]


def test_ranking_overwrites_whatever_score_the_model_invented():
    source = ResearchSource(
        title="t", url="https://x.dev", kind=ResourceKind.BLOG, summary="s", rank_score=9.9
    )

    assert rank_sources([source])[0].rank_score == 0.5


def test_ranking_keeps_the_models_order_within_one_kind():
    first = make_source("https://a.dev", ResourceKind.DOCS)
    second = make_source("https://b.dev", ResourceKind.DOCS)

    assert [s.url for s in rank_sources([first, second])] == ["https://a.dev", "https://b.dev"]
