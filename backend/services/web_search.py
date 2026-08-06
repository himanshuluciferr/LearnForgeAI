"""Finds real pages for a skill, so research retrieves instead of recalling.

A model can only propose links it saw during training, which is exactly why a course about a
framework released after its cutoff came back as a course about the framework before it.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from pydantic import BaseModel

from backend.workflow.state import ResourceKind

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10.0
USER_AGENT = "LearnForgeAI/1.0 (+course generator source search)"
RESULTS_PER_PROVIDER = 8

LEARN_SEARCH = "https://learn.microsoft.com/api/search"
GITHUB_SEARCH = "https://api.github.com/search/repositories"


class SearchHit(BaseModel):
    """One real page a search engine returned. The URL is never retyped by a model."""

    title: str
    url: str
    snippet: str
    kind: ResourceKind


async def search_learn(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
    response = await client.get(
        LEARN_SEARCH, params={"search": query, "locale": "en-us", "$top": limit}
    )
    response.raise_for_status()
    return [
        SearchHit(
            title=result.get("title") or result["url"],
            url=result["url"],
            snippet=result.get("description") or "",
            kind=ResourceKind.MICROSOFT_LEARN,
        )
        for result in (response.json().get("results") or [])
        if result.get("url")
    ]


async def _github(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
    response = await client.get(
        GITHUB_SEARCH,
        params={"q": query, "per_page": limit},
        headers={"accept": "application/vnd.github+json"},
    )
    response.raise_for_status()
    return [
        SearchHit(
            title=item["full_name"],
            url=item["html_url"],
            snippet=item.get("description") or "",
            kind=ResourceKind.GITHUB,
        )
        for item in response.json().get("items", [])
    ]


async def search_github(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
    """Two passes: the project itself, then what people built with it.

    Measured: GitHub's default ranking reads README text, so sample repositories bury the
    canonical one. Restricting to name and description is what surfaces microsoft/agent-framework.
    """
    named, broad = await asyncio.gather(
        _github(client, f"{query} in:name,description", limit),
        _github(client, query, limit),
    )
    return named + broad


PROVIDERS = (search_learn, search_github)


async def search_web(query: str, limit: int = RESULTS_PER_PROVIDER) -> list[SearchHit]:
    """Asks every provider at once. One outage narrows the results; it does not fail the job."""
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT, follow_redirects=True, headers={"user-agent": USER_AGENT}
    ) as client:
        answers = await asyncio.gather(
            *(provider(client, query, limit) for provider in PROVIDERS),
            return_exceptions=True,
        )

    hits: list[SearchHit] = []
    seen: set[str] = set()
    for provider, answer in zip(PROVIDERS, answers):
        if isinstance(answer, BaseException):
            logger.warning("Search provider %s failed: %s", provider.__name__, answer)
            continue
        for hit in answer:
            if hit.url not in seen:
                seen.add(hit.url)
                hits.append(hit)

    logger.info("Search for %r returned %d pages", query, len(hits))
    return hits
