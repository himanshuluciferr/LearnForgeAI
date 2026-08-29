"""Where the passages for a question come from.

Interface-first, the same shape as the stores: lexical when no search endpoint is configured,
Azure AI Search when one is. That keeps the offline suite offline and every local run working
with no Azure at all, and it makes the two comparable on the same questions rather than one
replacing the other on faith.

Lexical is not a degraded mode. Measured on a real course it answered six of six questions the
course covered, at 33 ms; search earns its place on scale and paraphrase, not by being newer.
"""

from __future__ import annotations

import logging
from typing import Protocol

from backend.services import ai_search
from backend.skills.passages.skill import passages_for, render
from backend.workflow.state import ResearchSource, ResourceKind

logger = logging.getLogger(__name__)


class Retriever(Protocol):
    async def passages(
        self, question: str, sources: list[ResearchSource], budget: int, **where: str
    ) -> str: ...


class LexicalRetriever:
    """Set cover over the text we already hold. No network, no index, deterministic."""

    async def passages(
        self, question: str, sources: list[ResearchSource], budget: int, **where: str
    ) -> str:
        return passages_for(sources, question, budget)


class SearchRetriever:
    """Hybrid search over the indexed passages of one course.

    Falls back to lexical when the index has nothing for this course — a course generated
    before the index existed, or one whose indexing failed. Answering from nothing because a
    backfill was missed would be a worse failure than being slower.
    """

    def __init__(self, lexical: Retriever | None = None) -> None:
        self._lexical = lexical or LexicalRetriever()

    async def passages(
        self, question: str, sources: list[ResearchSource], budget: int, **where: str
    ) -> str:
        course_id, user_id = where.get("course_id", ""), where.get("user_id", "")
        if not (course_id and user_id):
            return await self._lexical.passages(question, sources, budget)

        try:
            vector = await self._vector(question)
            rows = await ai_search.search_passages(question, course_id, user_id, vector)
        except Exception:
            logger.exception("ai-search: query failed, falling back to lexical")
            return await self._lexical.passages(question, sources, budget)

        if not rows:
            logger.info("ai-search: nothing indexed for course %s, using lexical", course_id)
            return await self._lexical.passages(question, sources, budget)
        return render(as_passages(rows, budget))

    @staticmethod
    async def _vector(question: str) -> list[float] | None:
        if not ai_search.vectors_enabled():
            return None
        from backend.services.embeddings import embed_one

        return await embed_one(question)


def as_passages(rows: list[dict], budget: int):
    """Search returns rows in relevance order; `render` wants Passages and regroups them by
    page so the model reads each source forwards rather than as a shuffled list."""
    from backend.skills.passages.skill import Passage

    chosen, spent = [], 0
    for order, row in enumerate(rows):
        text = row.get("text") or ""
        if spent + len(text) > budget:
            continue
        chosen.append(
            Passage(
                source=ResearchSource(
                    title=row.get("title") or "",
                    url=row.get("url") or "",
                    kind=ResourceKind.DOCS,
                    text=text,
                ),
                order=order,
                text=text,
                score=0,
            )
        )
        spent += len(text)
    return chosen


def get_retriever() -> Retriever:
    return SearchRetriever() if ai_search.search_enabled() else LexicalRetriever()
