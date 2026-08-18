"""Offline tests for the Learn documentation provider: parsing, de-duplication, failure."""

import json

import pytest

from backend.services import learn_docs
from backend.services.learn_docs import (
    MAX_CHARS_PER_DOCUMENT,
    MIN_PAGE_WORDS,
    as_samples,
    group_by_page,
    normalise_language,
    read_code_samples,
    read_learn_docs,
    results_in,
    split_title,
)


class Chunk:
    """Mirrors agent_framework's Content: the payload lives on `.text`."""

    def __init__(self, text: str) -> None:
        self.text = text


def chunk(*results: dict) -> Chunk:
    return Chunk(json.dumps({"results": list(results)}))


def result(url: str, content: str, title: str = "A page") -> dict:
    return {"title": title, "content": content, "contentUrl": url}


def body(words: int = MIN_PAGE_WORDS + 50) -> str:
    """A page long enough to survive the stub filter."""
    return "word " * words


# --- parsing -----------------------------------------------------------------------


def test_each_chunk_is_its_own_json_document():
    """Measured: the server returns several Content objects, each a complete document. Joining
    them into one string and parsing that raises 'Extra data'."""
    chunks = [chunk(result("https://learn.microsoft.com/a", "first")),
              chunk(result("https://learn.microsoft.com/b", "second"))]

    assert len(results_in(chunks)) == 2


def test_a_chunk_that_is_not_json_is_skipped_rather_than_fatal():
    assert results_in([Chunk("not json at all"), chunk(result("https://x/a", "kept"))]) != []


def test_no_chunks_is_no_results():
    assert results_in(None) == []
    assert results_in([]) == []


# --- de-duplication ----------------------------------------------------------------


def test_identical_chunks_are_counted_once():
    """Measured: the server returned the SAME 10 results twice, which silently doubled a
    retrieval measurement. Evidence volume decides how many topics a course can afford, so
    counting a passage twice buys topics there is no text for."""
    same = result("https://learn.microsoft.com/a", "the only passage")

    pages = group_by_page([same, same, same])

    assert len(pages) == 1
    assert pages[0].text == "the only passage"


def test_different_chunks_of_one_page_become_one_document():
    """The server answers in small chunks, so a page arrives in pieces."""
    pages = group_by_page(
        [
            result("https://learn.microsoft.com/a", "first half"),
            result("https://learn.microsoft.com/a", "second half"),
        ]
    )

    assert len(pages) == 1
    assert pages[0].text == "first half\n\nsecond half"


def test_a_fragment_is_the_same_page():
    pages = group_by_page(
        [
            result("https://learn.microsoft.com/a", "one"),
            result("https://learn.microsoft.com/a#section", "two"),
        ]
    )

    assert len(pages) == 1
    assert pages[0].url == "https://learn.microsoft.com/a"


def test_pages_keep_the_order_they_arrived_in():
    pages = group_by_page(
        [result("https://learn.microsoft.com/b", "x"), result("https://learn.microsoft.com/a", "y")]
    )

    assert [page.url for page in pages] == [
        "https://learn.microsoft.com/b",
        "https://learn.microsoft.com/a",
    ]


def test_a_result_with_no_url_or_no_content_is_dropped():
    pages = group_by_page(
        [
            {"title": "t", "content": "orphan"},
            result("https://learn.microsoft.com/a", "   "),
            result("https://learn.microsoft.com/b", "kept"),
        ]
    )

    assert [page.text for page in pages] == ["kept"]


# --- language variants -------------------------------------------------------------


def test_the_language_variant_is_read_off_the_title():
    assert split_title("Agent Middleware (programming-language-csharp)") == (
        "Agent Middleware",
        "csharp",
    )


def test_a_page_with_no_variant_is_language_neutral():
    """Neutral pages exist and suit any course: 'Adding Middleware' carries no suffix."""
    assert split_title("Adding Middleware") == ("Adding Middleware", "")


def test_the_variant_suffix_is_stripped_from_the_title():
    """The title rides into every downstream prompt, where the suffix is noise."""
    title, _ = split_title("Agent pipeline architecture (programming-language-python)")

    assert "programming-language" not in title


@pytest.mark.parametrize(
    "spoken,expected",
    [("C#", "csharp"), ("c sharp", "csharp"), (".NET", "csharp"), ("Python", "python"),
     ("py", "python"), ("TypeScript", "typescript"), ("rust", "rust")],
)
def test_the_learners_name_for_a_language_maps_to_the_one_learn_uses(spoken, expected):
    assert normalise_language(spoken) == expected


def test_the_two_variants_of_one_page_do_not_become_one_document():
    """Measured: both variants carry the SAME contentUrl, so grouping by url alone merged C#
    and Python prose into one source and the course switched language between topics."""
    url = "https://learn.microsoft.com/agent-framework/concepts/agents/middleware/"

    pages = group_by_page(
        [
            result(url, "var agent = new Agent();", "Agent Middleware (programming-language-csharp)"),
            result(url, "agent = Agent()", "Agent Middleware (programming-language-python)"),
        ]
    )

    assert len(pages) == 2
    assert {page.language for page in pages} == {"csharp", "python"}
    assert all(page.url == url for page in pages)


def test_a_fragment_still_joins_its_own_variant():
    """The fragment is stripped, so #function-calling-middleware is the same C# page."""
    url = "https://learn.microsoft.com/agent-framework/concepts/agents/middleware/"

    pages = group_by_page(
        [
            result(url, "first", "Agent Middleware (programming-language-csharp)"),
            result(f"{url}#function-calling", "second", "Agent Middleware (programming-language-csharp)"),
            result(f"{url}#termination", "third", "Agent Middleware (programming-language-python)"),
        ]
    )

    assert len(pages) == 2
    csharp = next(page for page in pages if page.language == "csharp")
    assert csharp.text == "first\n\nsecond"


def test_one_page_cannot_swallow_the_whole_evidence_budget():
    pages = group_by_page([result("https://learn.microsoft.com/a", "w" * 200_000)])

    assert len(pages[0].text) == MAX_CHARS_PER_DOCUMENT


# --- calling the server ------------------------------------------------------------


class StubMcp:
    def __init__(self, chunks=None, error: Exception | None = None) -> None:
        self.chunks = chunks or []
        self.error = error
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def call_tool(self, tool: str, **arguments):
        self.calls.append({"tool": tool, **arguments})
        if self.error:
            raise self.error
        return self.chunks


@pytest.mark.asyncio
async def test_no_queries_means_no_server_call(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("the server must not be contacted")

    monkeypatch.setattr(learn_docs, "MCPStreamableHTTPTool", fail)

    assert await read_learn_docs([]) == []


@pytest.mark.asyncio
async def test_every_query_is_asked_and_the_pages_come_back(monkeypatch):
    stub = StubMcp(chunks=[chunk(result("https://learn.microsoft.com/a", body()))])
    monkeypatch.setattr(learn_docs, "MCPStreamableHTTPTool", lambda **kwargs: stub)

    pages = await read_learn_docs(["one", "two"])

    assert [call["query"] for call in stub.calls] == ["one", "two"]
    assert len(pages) == 1  # the same page from both queries is still one page


@pytest.mark.asyncio
async def test_the_parameter_is_query_not_question(monkeypatch):
    """Measured: the wrong parameter name returns an empty result set silently rather than
    erroring, which reads as 'nothing exists' instead of 'we asked wrongly'."""
    stub = StubMcp()
    monkeypatch.setattr(learn_docs, "MCPStreamableHTTPTool", lambda **kwargs: stub)

    await read_learn_docs(["anything"])

    assert "query" in stub.calls[0]


@pytest.mark.asyncio
async def test_a_failing_query_narrows_the_evidence_rather_than_failing_the_job(monkeypatch):
    stub = StubMcp(error=RuntimeError("upstream is down"))
    monkeypatch.setattr(learn_docs, "MCPStreamableHTTPTool", lambda **kwargs: stub)

    assert await read_learn_docs(["one"]) == []


@pytest.mark.asyncio
async def test_an_unreachable_server_is_not_fatal(monkeypatch):
    def explode(**kwargs):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(learn_docs, "MCPStreamableHTTPTool", explode)

    assert await read_learn_docs(["one"]) == []


@pytest.mark.asyncio
async def test_the_page_limit_is_honoured(monkeypatch):
    many = [result(f"https://learn.microsoft.com/{n}", body()) for n in range(30)]
    stub = StubMcp(chunks=[chunk(*many)])
    monkeypatch.setattr(learn_docs, "MCPStreamableHTTPTool", lambda **kwargs: stub)

    assert len(await read_learn_docs(["one"], limit=5)) == 5


@pytest.mark.asyncio
async def test_a_stub_page_does_not_consume_a_source_slot(monkeypatch):
    """Measured on run 7: 7 of 20 sources were under 300 words. A landing page or a one-line
    stub is not a source, and it displaces a page that could have taught something."""
    stub = StubMcp(
        chunks=[
            chunk(
                result("https://learn.microsoft.com/stub", "barely anything here"),
                result("https://learn.microsoft.com/real", body()),
            )
        ]
    )
    monkeypatch.setattr(learn_docs, "MCPStreamableHTTPTool", lambda **kwargs: stub)

    pages = await read_learn_docs(["one"])

    assert [page.url for page in pages] == ["https://learn.microsoft.com/real"]


# --- code samples ------------------------------------------------------------------


def sample(snippet: str, link: str = "https://learn.microsoft.com/s", description: str = "How to") -> dict:
    return {"description": description, "codeSnippet": snippet, "link": link, "language": "python"}


def code(*samples: dict) -> Chunk:
    return Chunk(json.dumps({"results": list(samples)}))


def test_a_snippet_becomes_a_document_carrying_its_language():
    docs = as_samples([sample("agent = Agent(client=client)\n" * 8)], "python")

    assert len(docs) == 1
    assert docs[0].language == "python"
    assert "```python" in docs[0].text


def test_the_same_snippet_returned_by_two_queries_is_kept_once():
    one = sample("agent = Agent(client=client)\n" * 8)

    assert len(as_samples([one, dict(one)], "python")) == 1


def test_a_snippet_too_short_to_teach_anything_is_dropped():
    """A single import line costs prompt budget and shows nothing."""
    assert as_samples([sample("import agent_framework")], "python") == []


def test_a_snippet_with_no_link_is_dropped():
    orphan = sample("agent = Agent(client=client)\n" * 8, link="")

    assert as_samples([orphan], "python") == []


def test_the_servers_own_field_name_is_stripped_from_the_title():
    """Measured: descriptions come back as 'description: Defines and runs an AI agent...',
    and the title is carried into every prompt that cites the sample."""
    described = sample("agent = Agent(client=client)\n" * 8, description="description: Runs an agent")

    assert as_samples([described], "python")[0].title == "Runs an agent"


@pytest.mark.asyncio
async def test_code_samples_are_requested_in_the_courses_language(monkeypatch):
    """The server filters by language, which is why samples cannot skew the course the way
    the prose pages did."""
    stub = StubMcp(chunks=[code(sample("agent = Agent(client=client)\n" * 8))])
    monkeypatch.setattr(learn_docs, "MCPStreamableHTTPTool", lambda **kwargs: stub)

    await read_code_samples(["one"], "C#")

    assert stub.calls[0]["language"] == "csharp"


@pytest.mark.asyncio
async def test_no_queries_means_no_sample_call(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("the server must not be contacted")

    monkeypatch.setattr(learn_docs, "MCPStreamableHTTPTool", fail)

    assert await read_code_samples([], "python") == []


@pytest.mark.asyncio
async def test_missing_samples_narrow_the_evidence_rather_than_failing_the_job(monkeypatch):
    stub = StubMcp(error=RuntimeError("upstream is down"))
    monkeypatch.setattr(learn_docs, "MCPStreamableHTTPTool", lambda **kwargs: stub)

    assert await read_code_samples(["one"], "python") == []
