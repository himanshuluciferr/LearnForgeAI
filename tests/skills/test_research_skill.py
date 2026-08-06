"""Offline tests for the research and ranking skills: URL safety, liveness, topic, ordering."""

import httpx
import pytest

from backend.skills.ranking.skill import rank_sources
from backend.skills.research.skill import (
    MAX_CHARS,
    inspect_source,
    is_fetchable,
    phrase,
    verify_sources,
    wanted_phrases,
)
from backend.workflow.state import ResearchSource, ResourceKind


def make_source(url: str = "https://learn.microsoft.com/azure/search/", kind=ResourceKind.DOCS):
    return ResearchSource(title="t", url=url, kind=kind, summary="s")


def serving(body: str = "", status: int = 200):
    return httpx.MockTransport(lambda request: httpx.Response(status, text=body))


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
    assert await verify_sources([make_source("https://169.254.169.254/latest/")], "azure") == []


@pytest.mark.asyncio
async def test_verify_sources_handles_an_empty_list():
    assert await verify_sources([], "azure") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [(200, True), (301, True), (404, False), (410, False), (500, False)],
)
async def test_reachability_follows_the_status_code(status, expected):
    transport = serving(status=status)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        assert await inspect_source(client, make_source(), wanted_phrases("azure")) is expected


@pytest.mark.asyncio
async def test_a_network_error_drops_the_source_instead_of_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await inspect_source(client, make_source(), wanted_phrases("azure")) is False


# --- is the page about the skill at all ---


@pytest.mark.asyncio
async def test_a_page_that_names_the_skill_is_marked_on_topic():
    source = make_source()
    transport = serving("Welcome to Microsoft Agent Framework.")

    async with httpx.AsyncClient(transport=transport) as client:
        await inspect_source(client, source, wanted_phrases("Microsoft Agent Framework"))

    assert source.mentions_skill is True


@pytest.mark.asyncio
async def test_documentation_that_drops_the_vendor_name_still_counts():
    # Measured: learn.microsoft.com/agent-framework/ is titled "Agent Framework documentation".
    source = make_source()
    transport = serving("Agent Framework documentation | Microsoft Learn")

    async with httpx.AsyncClient(transport=transport) as client:
        await inspect_source(client, source, wanted_phrases("Microsoft Agent Framework"))

    assert source.mentions_skill is True


@pytest.mark.asyncio
async def test_a_live_page_about_a_different_product_is_kept_but_not_marked_on_topic():
    # The exact failure this check exists for: every Bot Framework URL the model proposed
    # was real and returned 200, so reachability alone waved all of them through.
    source = make_source("https://learn.microsoft.com/azure/bot-service/")
    page = "Azure Bot Service documentation. Build bots with the Bot Framework SDK."

    async with httpx.AsyncClient(transport=serving(page)) as client:
        assert await inspect_source(
            client, source, wanted_phrases("Microsoft Agent Framework")
        ) is True

    assert source.mentions_skill is False


@pytest.mark.asyncio
async def test_markup_between_the_words_does_not_hide_the_phrase():
    source = make_source()
    transport = serving("<h1>Microsoft <b>Agent</b> Framework</h1>")

    async with httpx.AsyncClient(transport=transport) as client:
        await inspect_source(client, source, wanted_phrases("Microsoft Agent Framework"))

    assert source.mentions_skill is True


@pytest.mark.asyncio
async def test_a_longer_word_does_not_count_as_a_mention():
    source = make_source()

    async with httpx.AsyncClient(transport=serving("We are going to the shops.")) as client:
        await inspect_source(client, source, wanted_phrases("Go"))

    assert source.mentions_skill is False


@pytest.mark.asyncio
async def test_a_mention_past_the_size_cap_is_not_read():
    source = make_source()
    page = f"{'padding ' * (MAX_CHARS // 8)}Microsoft Agent Framework"

    async with httpx.AsyncClient(transport=serving(page)) as client:
        await inspect_source(client, source, wanted_phrases("Microsoft Agent Framework"))

    assert source.mentions_skill is False


def test_a_three_word_product_can_also_be_found_without_its_vendor():
    assert wanted_phrases("Microsoft Agent Framework") == (
        " microsoft agent framework ",
        " agent framework ",
    )


def test_trimming_the_vendor_never_leaves_a_single_generic_word():
    # "Azure Functions" must not be satisfied by any page that says "functions".
    assert wanted_phrases("Azure Functions") == (" azure functions ",)


def test_a_leading_word_that_is_not_a_vendor_is_kept():
    assert wanted_phrases("Clean Code Principles") == (" clean code principles ",)


@pytest.mark.parametrize(
    "written",
    ["microsoft agent framework", "Microsoft  Agent\nFramework", "Microsoft-Agent-Framework"],
)
def test_spacing_case_and_punctuation_do_not_change_the_phrase(written):
    assert phrase(written) == phrase("Microsoft Agent Framework")


@pytest.mark.asyncio
async def test_verify_sources_asks_every_page_about_the_skill_it_was_given(monkeypatch):
    asked: list[str] = []

    async def spy(client, source, wanted):
        asked.append(wanted)
        return True

    monkeypatch.setattr("backend.skills.research.skill.inspect_source", spy)

    await verify_sources([make_source("https://a.dev"), make_source("https://b.dev")], "Rust")

    assert asked == [wanted_phrases("Rust"), wanted_phrases("Rust")]


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
