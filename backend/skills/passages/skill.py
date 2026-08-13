"""Chooses the parts of a source that one chapter actually needs.

A chapter used to receive the first N characters of every source. For a reference page that is
its introduction, every time — so a chapter on `--rebase-merges` was written without ever seeing
the `--rebase-merges` section, although we had fetched and stored it. Measured end to end, the
writer saw 19-21% of what research retrieved, always from the top.

This is lexical retrieval, so it is code: no model call, no network, and the same input always
gives the same passages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.workflow.state import ResearchSource

# Long enough to hold an explanation, short enough that one chapter's worth is several places
# in the document rather than one long run.
WORDS_PER_PASSAGE = 150
# A paragraph that straddles a boundary is still found whole by one window or the other.
OVERLAP_WORDS = 25

MIN_TERM_LENGTH = 3

# Without these, every passage matches every chapter and the ranking says nothing.
STOPWORDS = frozenset(
    """a an and are as at be but by can for from had has have how in into is it its may not of
    on or that the their them then there these this to use used using was what when where which
    will with you your""".split()
)

WORD = re.compile(r"[a-z0-9]+")


@dataclass
class Passage:
    source: ResearchSource
    order: int
    text: str
    score: int


def terms(text: str) -> set[str]:
    """Distinct words worth matching on. A set, not a count: frequency would reward long,
    repetitive navigation blocks over a short passage that answers the question."""
    return {
        word
        for word in WORD.findall(text.lower())
        if len(word) >= MIN_TERM_LENGTH and word not in STOPWORDS
    }


def split(text: str) -> list[str]:
    """Fixed overlapping windows, because `extract_text` collapses all whitespace and there are
    no paragraph breaks left to split on."""
    words = text.split()
    if not words:
        return []
    step = WORDS_PER_PASSAGE - OVERLAP_WORDS
    return [
        " ".join(words[start : start + WORDS_PER_PASSAGE])
        for start in range(0, len(words), step)
    ]


def passages_of(source: ResearchSource, wanted: set[str]) -> list[Passage]:
    return [
        Passage(source=source, order=order, text=text, score=len(terms(text) & wanted))
        for order, text in enumerate(split(source.text))
    ]


def head_of(sources: list[ResearchSource], budget: int) -> list[Passage]:
    """What the writer used to get: the top of every source. Kept only for the case where a
    chapter's words match nothing at all, so it is degraded rather than empty."""
    share = max(1, budget // max(1, len(sources)))
    return [
        Passage(source=source, order=0, text=source.text[:share], score=0) for source in sources
    ]


def select(sources: list[ResearchSource], query: str, budget: int) -> list[Passage]:
    """Most relevant first, until the budget is spent."""
    wanted = terms(query)
    scored = [passage for source in sources for passage in passages_of(source, wanted)]
    # Stable, so passages that tie on score stay in document order.
    scored.sort(key=lambda passage: passage.score, reverse=True)

    chosen: list[Passage] = []
    spent = 0
    for passage in scored:
        if passage.score == 0:
            break
        if spent + len(passage.text) > budget:
            continue
        chosen.append(passage)
        spent += len(passage.text)

    return chosen or head_of(sources, budget)


def render(passages: list[Passage]) -> str:
    """Grouped by source and back in document order, so the writer reads each page forwards and
    can still tell which page a claim came from."""
    if not passages:
        return "None."

    grouped: dict[str, list[Passage]] = {}
    for passage in passages:
        grouped.setdefault(passage.source.url, []).append(passage)

    blocks = []
    for url, items in grouped.items():
        items.sort(key=lambda passage: passage.order)
        body = items[0].text
        for previous, passage in zip(items, items[1:]):
            # The marker matters: without it the writer reads a jump cut as continuous prose.
            joiner = " " if passage.order == previous.order + 1 else " [...] "
            body += joiner + passage.text
        blocks.append(f"{items[0].source.title} ({url})\n{body}")
    return "\n\n".join(blocks)


def passages_for(sources: list[ResearchSource], query: str, budget: int) -> str:
    return render(select(sources, query, budget))
