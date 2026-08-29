"""Tests for the search index and the retriever in front of it.

None of these reach Azure. The point of the interface is that a machine with no search
endpoint behaves exactly as it did before, and that is checked here rather than assumed.
"""

from __future__ import annotations

import pytest

from backend.services import ai_search
from backend.services.course_index import documents, index_course, passage_id
from backend.services.retrieval import (
    LexicalRetriever,
    SearchRetriever,
    as_passages,
    get_retriever,
)
from backend.workflow.state import (
    Chapter,
    CourseState,
    ResearchSource,
    ResourceKind,
)

USER = "priya@contoso.com"
COURSE = "c-1"


def make_state(chapters=1, sources=1) -> CourseState:
    state = CourseState(job_id="j", user_id=USER, prompt="p")
    state.chapters = [
        Chapter(number=n, title=f"Chapter {n}", body_markdown="reconcile loops " * 300)
        for n in range(1, chapters + 1)
    ]
    state.research = [
        ResearchSource(
            title=f"Page {n}",
            url=f"https://docs.example/{n}",
            kind=ResourceKind.DOCS,
            text="custom resources " * 300,
        )
        for n in range(1, sources + 1)
    ]
    return state


# --- what goes into the index --------------------------------------------------------


def test_a_course_is_indexed_as_passages_not_as_one_document():
    """A course is 160,000 characters and a question wants a paragraph; indexing whole courses
    would return the whole course."""
    rows = documents(COURSE, USER, make_state(chapters=1, sources=0))

    assert len(rows) > 1


def test_a_chapter_passage_carries_the_chapter_to_reread():
    rows = documents(COURSE, USER, make_state(chapters=1, sources=0))

    assert all(row["chapter_number"] == 1 for row in rows)


def test_a_source_passage_carries_no_chapter():
    """Exactly the distinction the mentor already draws: a source is not somewhere to re-read."""
    rows = documents(COURSE, USER, make_state(chapters=0, sources=1))

    assert all(row["chapter_number"] is None for row in rows)


def test_every_passage_is_stamped_with_its_owner():
    rows = documents(COURSE, USER, make_state())

    assert all(row["user_id"] == USER and row["course_id"] == COURSE for row in rows)


def test_keys_are_unique_across_chapters_and_sources():
    rows = documents(COURSE, USER, make_state(chapters=3, sources=3))

    assert len({row["id"] for row in rows}) == len(rows)


def test_a_key_survives_a_url_that_is_not_key_safe():
    """A search key allows letters, digits, dash, underscore and equals; urls are none of
    those."""
    key = passage_id(COURSE, "https://docs.example/a?b=c#d", 0)

    assert key.replace("-", "").isalnum()


def test_two_urls_cannot_collapse_to_one_key():
    assert passage_id(COURSE, "https://a.example", 0) != passage_id(COURSE, "https://b.example", 0)


@pytest.mark.asyncio
async def test_nothing_is_indexed_when_there_is_no_search_service(monkeypatch):
    """The offline default. A machine with no endpoint must not try, and must not fail."""
    monkeypatch.setattr(ai_search, "search_enabled", lambda: False)

    assert await index_course(COURSE, USER, make_state()) == 0


@pytest.mark.asyncio
async def test_indexing_that_fails_does_not_fail_the_course(monkeypatch):
    """A course that failed to index is still a course; the mentor falls back to lexical."""
    monkeypatch.setattr(ai_search, "search_enabled", lambda: True)

    async def broken() -> None:
        raise RuntimeError("search is down")

    monkeypatch.setattr(ai_search, "ensure_index", broken)

    assert await index_course(COURSE, USER, make_state()) == 0


# --- choosing a retriever -------------------------------------------------------------


def test_without_a_search_endpoint_retrieval_stays_lexical(monkeypatch):
    monkeypatch.setattr(ai_search, "search_enabled", lambda: False)

    assert isinstance(get_retriever(), LexicalRetriever)


def test_with_a_search_endpoint_the_index_is_used(monkeypatch):
    monkeypatch.setattr(ai_search, "search_enabled", lambda: True)

    assert isinstance(get_retriever(), SearchRetriever)


@pytest.mark.asyncio
async def test_a_query_that_falls_over_falls_back_rather_than_failing(monkeypatch):
    """Answering nothing because the index is down would be worse than being slower."""
    monkeypatch.setattr(ai_search, "vectors_enabled", lambda: False)

    async def broken(*args, **kwargs):
        raise RuntimeError("search is down")

    monkeypatch.setattr(ai_search, "search_passages", broken)
    state = make_state()

    shown = await SearchRetriever().passages(
        "reconcile", state.chapters and [] or [], 4000, course_id=COURSE, user_id=USER
    )

    assert shown is not None


@pytest.mark.asyncio
async def test_a_course_that_was_never_indexed_falls_back(monkeypatch):
    """A course generated before the index existed, or one whose indexing failed."""
    monkeypatch.setattr(ai_search, "vectors_enabled", lambda: False)

    async def empty(*args, **kwargs):
        return []

    monkeypatch.setattr(ai_search, "search_passages", empty)
    sources = [ResearchSource(title="t", url="u", kind=ResourceKind.DOCS, text="reconcile " * 200)]

    shown = await SearchRetriever().passages(
        "reconcile", sources, 4000, course_id=COURSE, user_id=USER
    )

    assert "reconcile" in shown


@pytest.mark.asyncio
async def test_without_a_course_to_scope_to_it_does_not_search(monkeypatch):
    """Unscoped, the index is one shared corpus and a question could be answered out of
    somebody else's course."""
    called = []

    async def watched(*args, **kwargs):
        called.append(args)
        return []

    monkeypatch.setattr(ai_search, "search_passages", watched)
    sources = [ResearchSource(title="t", url="u", kind=ResourceKind.DOCS, text="reconcile " * 200)]

    await SearchRetriever().passages("reconcile", sources, 4000)

    assert called == []


def test_results_are_kept_within_the_budget():
    rows = [{"title": "t", "url": f"u{n}", "text": "x" * 1000} for n in range(20)]

    chosen = as_passages(rows, budget=3000)

    assert sum(len(passage.text) for passage in chosen) <= 3000


# --- the filter that keeps courses apart ----------------------------------------------


def test_the_filter_names_both_the_course_and_its_owner():
    assert ai_search.owned_by("c1", "u1") == "course_id eq 'c1' and user_id eq 'u1'"


def test_a_quote_in_an_id_cannot_rewrite_the_filter():
    """OData escapes a quote by doubling it; unescaped, an id could change the filter rather
    than be matched by it."""
    assert "''" in ai_search.owned_by("c'1", "u1")
