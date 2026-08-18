"""Tests for passage selection: the chapter gets the part of the page it needs.

Measured before this existed: the writer saw 19-21% of what research retrieved, always the
first characters of every source. For a reference page that is its introduction every time, so
a chapter on `--rebase-merges` was written without ever seeing the `--rebase-merges` section.
"""

from __future__ import annotations

import pytest

from backend.skills.passages.skill import (
    OVERLAP_WORDS,
    WORDS_PER_PASSAGE,
    head_of,
    passages_for,
    render,
    select,
    split,
    terms,
)
from backend.workflow.state import ResearchSource, ResourceKind


def source(text: str, url: str = "https://x.example/a", title: str = "t") -> ResearchSource:
    return ResearchSource(title=title, url=url, kind=ResourceKind.DOCS, text=text)


def words(count: int, word: str = "filler") -> str:
    return " ".join([word] * count)


# --- terms ---


def test_stopwords_are_dropped_so_ranking_says_something():
    """Without this every passage matches every chapter."""
    assert terms("The rebase is in the branch") == {"rebase", "branch"}


def test_very_short_tokens_are_dropped():
    assert "a" not in terms("a rebase")
    assert "of" not in terms("of rebase")


def test_terms_are_a_set_so_repetition_does_not_win():
    """Frequency would reward a long repetitive navigation block over a short answer."""
    assert terms("rebase rebase rebase") == {"rebase"}


# --- splitting ---


def test_a_short_page_is_one_passage():
    assert len(split(words(10))) == 1


def test_a_long_page_is_split_into_overlapping_windows():
    pieces = split(words(WORDS_PER_PASSAGE * 3))

    assert len(pieces) > 3  # overlap means more windows than a clean division would give
    assert all(len(piece.split()) <= WORDS_PER_PASSAGE for piece in pieces)


def test_windows_overlap_so_a_passage_on_a_boundary_survives_whole():
    numbered = " ".join(str(n) for n in range(WORDS_PER_PASSAGE * 2))
    pieces = split(numbered)

    first_end = pieces[0].split()[-OVERLAP_WORDS:]

    assert all(word in pieces[1].split() for word in first_end)


def test_an_empty_page_yields_nothing():
    assert split("") == []


# --- selection ---


def test_the_passage_about_the_chapter_is_chosen_over_the_start_of_the_page():
    """The whole point. The introduction is first in the document and must not win on that."""
    page = source(f"{words(WORDS_PER_PASSAGE * 2, 'introduction')} rebase merges topology")

    chosen = select([page], "rebase merges topology", budget=10_000)

    assert "rebase merges topology" in chosen[0].text


def test_selection_stops_at_the_budget():
    page = source(" ".join(f"rebase item{n}" for n in range(2_000)))

    chosen = select([page], "rebase", budget=2_000)

    assert sum(len(passage.text) for passage in chosen) <= 2_000


def test_passages_that_match_nothing_are_not_included():
    page = source(f"{words(300, 'unrelated')} rebase")

    chosen = select([page], "rebase", budget=100_000)

    assert all("rebase" in passage.text for passage in chosen)


def test_a_chapter_matching_nothing_still_gets_material():
    """Degraded rather than empty: the writer falls back to the head of each source."""
    page = source(words(400, "unrelated"))

    chosen = select([page], "kubernetes operators", budget=1_000)

    assert chosen
    assert all(passage.score == 0 for passage in chosen)


def test_the_fallback_shares_the_budget_between_sources():
    pages = [source(words(5_000), url=f"https://x{n}.example") for n in range(4)]

    chosen = head_of(pages, budget=4_000)

    assert len(chosen) == 4
    assert sum(len(passage.text) for passage in chosen) <= 4_000


def test_ties_keep_document_order():
    page = source(" ".join(f"rebase part{n} {words(WORDS_PER_PASSAGE)}" for n in range(4)))

    chosen = select([page], "rebase", budget=100_000)
    orders = [passage.order for passage in chosen]

    assert orders == sorted(orders)


def test_a_rare_term_is_bought_even_though_a_common_one_scores_higher():
    """The whole reason this is set cover. Ranking by raw overlap filled the budget with
    passages that all repeated the same two common terms, and the single mention of the rare
    one was never reached: measured over a finished course, 65% of a topic's vocabulary
    against an 87% ceiling."""
    common = source(" ".join(["alpha beta"] * 200), url="https://common.dev")
    rare = source(f"{words(WORDS_PER_PASSAGE - 4)} gamma", url="https://rare.dev")
    # Room for three passages, and four of the common ones outscore the rare one.
    budget = 3 * len(split(common.text)[0])

    chosen = select([common, rare], "alpha beta gamma", budget)

    assert any("gamma" in passage.text for passage in chosen)


def test_leftover_budget_is_spent_once_there_is_nothing_new_to_cover():
    """Stopping at full coverage would hand a narrow topic a fraction of its budget."""
    page = source(" ".join(f"rebase item{n}" for n in range(2_000)))

    chosen = select([page], "rebase", budget=6_000)
    spent = sum(len(passage.text) for passage in chosen)

    assert spent > 4_000
    assert spent <= 6_000


def test_backfill_never_reaches_for_a_passage_that_matches_nothing():
    page = source(f"rebase {words(600, 'unrelated')}")

    chosen = select([page], "rebase", budget=100_000)

    assert all("rebase" in passage.text for passage in chosen)


# --- rendering ---


def test_each_block_names_the_page_it_came_from():
    page = source("rebase merges", url="https://git-scm.com/docs/git-rebase", title="git-rebase")

    rendered = passages_for([page], "rebase merges", budget=10_000)

    assert "git-rebase (https://git-scm.com/docs/git-rebase)" in rendered


def test_skipped_text_is_marked_so_a_jump_cut_does_not_read_as_prose():
    filler = words(WORDS_PER_PASSAGE * 4, "unrelated")
    page = source(f"rebase start {filler} rebase end")

    rendered = passages_for([page], "rebase", budget=100_000)

    assert "[...]" in rendered


def test_adjacent_passages_are_joined_without_a_marker():
    page = source(f"rebase {words(WORDS_PER_PASSAGE * 2, 'rebase')}")

    rendered = passages_for([page], "rebase", budget=100_000)

    assert "[...]" not in rendered


def test_no_sources_renders_as_none():
    assert render([]) == "None."


@pytest.mark.parametrize("budget", [500, 5_000, 50_000])
def test_the_budget_is_never_exceeded(budget):
    pages = [
        source(" ".join(f"rebase merges item{n}" for n in range(3_000)), url=f"https://x{i}.dev")
        for i in range(3)
    ]

    rendered = passages_for(pages, "rebase merges", budget)

    assert len(rendered) < budget * 1.2  # plus the per-source headings
