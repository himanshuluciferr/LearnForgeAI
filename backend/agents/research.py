"""research-agent — gathers the pages the course will actually be written from.

Node 2 established *what* the subject is. This node gathers enough to *teach* it, and the
difference that matters is that it keeps the page text. Before this, the node asked a model to
propose URLs from memory, checked that something answered a HEAD request, and passed on a
title, a URL and a summary the same model had written about a page it had never opened.

Search and fetch are ours; the model only chooses which of the results we found are worth
reading, and it chooses them by number.
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

from agent_framework import Agent, Executor, WorkflowContext, handler

from backend.prompts.loader import load_prompt
from backend.services.foundry import get_chat_client
from backend.services.page_fetch import fetch_documents
from backend.services.web_search import SearchHit, dedupe, host_of, pick, search_web
from backend.skills.ranking.skill import rank_sources
from backend.workflow.state import (
    CourseState,
    LearningRequest,
    ResearchSource,
    ResourceKind,
    SourceSelection,
    SubjectAnalysis,
    WorkflowStep,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "research-agent"

# One search for the subject itself, then one per area it covers. Each hosted search is a billed
# call of a few tens of seconds, so the ground covered is bounded here rather than by however
# many areas node 2 happened to name.
MAX_QUERIES = 4
# Caps outbound fetches, and caps how much text every downstream prompt has to carry.
MAX_SOURCES = 8


@lru_cache
def get_research_agent() -> Agent:
    return get_chat_client().as_agent(
        name=AGENT_NAME,
        instructions=load_prompt("research"),
        default_options={"response_format": SourceSelection},
    )


def plan_queries(subject: SubjectAnalysis) -> list[str]:
    """The subject itself, then the areas node 2 found it to cover — which came from pages we
    read, so the queries are grounded rather than guessed."""
    name = subject.canonical_name or ""
    return [name] + [f"{name} {area}" for area in subject.scope[: MAX_QUERIES - 1]]


def classify(url: str) -> ResourceKind:
    """Read off the host, because asking a model for something we can see would be asking it
    to guess at a fact we already hold."""
    host = host_of(url)
    if host.endswith("learn.microsoft.com"):
        return ResourceKind.MICROSOFT_LEARN
    if host.endswith("github.com"):
        return ResourceKind.GITHUB
    if host.endswith(("youtube.com", "youtu.be", "vimeo.com")):
        return ResourceKind.VIDEO
    if host.endswith(("medium.com", "dev.to", "substack.com")) or host.startswith("blog."):
        return ResourceKind.BLOG
    return ResourceKind.DOCS


async def collect(subject: SubjectAnalysis) -> list[SearchHit]:
    queries = plan_queries(subject)
    gathered = await asyncio.gather(
        *(search_web(query) for query in queries), return_exceptions=True
    )
    hits: list[SearchHit] = []
    for query, result in zip(queries, gathered):
        if isinstance(result, BaseException):
            logger.warning("research-agent: search failed for %r: %r", query, result)
        else:
            hits.extend(result)
    return dedupe(hits)


def number_hits(hits: list[SearchHit]) -> str:
    return "\n".join(
        f"[{number}] {hit.title}\n    {hit.url}\n    {hit.snippet[:180]}"
        for number, hit in enumerate(hits, start=1)
    )


def build_prompt(request: LearningRequest, subject: SubjectAnalysis, hits: list[SearchHit]) -> str:
    return (
        f"Subject: {subject.canonical_name or request.skill}\n"
        f"What it is: {subject.description}\n"
        f"Areas it covers: {', '.join(subject.scope) or 'not established'}\n"
        f"Learner's current level: {request.assumed_level}\n"
        f"Goal: {request.goal or 'not stated'}\n"
        f"Choose at most {MAX_SOURCES}.\n\n"
        f"{len(hits)} search results:\n\n{number_hits(hits)}"
    )


async def select_sources(
    request: LearningRequest, subject: SubjectAnalysis, hits: list[SearchHit]
) -> SourceSelection:
    response = await get_research_agent().run(build_prompt(request, subject, hits))
    return response.value


async def gather_sources(
    request: LearningRequest, subject: SubjectAnalysis
) -> list[ResearchSource]:
    """Search, select by number, fetch, keep the text.

    Empty is a failure rather than a degraded pass. Once chapters are written FROM retrieved
    text, quietly falling back to "write from general knowledge" would make the grounding
    optional, which is the thing this node exists to stop.
    """
    named = subject.canonical_name or request.skill
    hits = await collect(subject)
    if not hits:
        raise ValueError(f"research found nothing for {named!r}")

    selection = await select_sources(request, subject, hits)
    chosen = pick(hits, selection.picks, MAX_SOURCES)
    if not chosen:
        raise ValueError(f"research-agent selected none of the {len(hits)} results for {named!r}")

    documents = await fetch_documents(chosen, limit=MAX_SOURCES)
    sources = [
        ResearchSource(
            title=document.title,
            url=document.url,
            kind=classify(document.url),
            text=document.text,
        )
        for document in documents
    ]
    if not sources:
        raise ValueError(f"research-agent could not read any source it selected for {named!r}")

    logger.info(
        "research-agent: %d results -> %d selected -> %d read, %d words",
        len(hits),
        len(chosen),
        len(sources),
        sum(source.words for source in sources),
    )
    return rank_sources(sources)


class ResearchExecutor(Executor):
    """Graph node for research-agent."""

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState]) -> None:
        assert state.request is not None and state.subject is not None
        state.research = await gather_sources(state.request, state.subject)
        state.mark(WorkflowStep.RESEARCH)
        await ctx.send_message(state)
