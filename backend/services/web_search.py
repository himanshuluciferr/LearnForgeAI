"""Web search behind one capability: `search_web(query, domains=None)`.

Microsoft Learn and GitHub are not services a caller chooses between — they are domains a
caller may ask for, and the router picks the adapter whose API *is* that domain restriction.

The hosted tool cannot do this itself: its schema is `{'type': 'web_search'}` with no
parameters, and `site:` is advisory. Measured, a `site:learn.microsoft.com` query still
returned a github.com citation and `site:wikipedia.org` returned nothing at all, so a domain
filter passed into the query is a promise the transport cannot keep.
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Awaitable, Callable, Sequence
from urllib.parse import urlparse

import httpx
from agent_framework import Agent
from pydantic import BaseModel

from backend.config.settings import get_settings
from backend.services.foundry import get_chat_client

logger = logging.getLogger(__name__)

LEARN_URL = "https://learn.microsoft.com/api/search"
GITHUB_URL = "https://api.github.com/search/repositories"
TIMEOUT_SECONDS = 20.0

SEARCH_AGENT_NAME = "subject-search-agent"
SEARCH_AGENT_INSTRUCTIONS = (
    "Search the web for the name you are given and cite what you find. Keep prose short — "
    "only the citations are read."
)


class SearchHit(BaseModel):
    """One candidate source. `url` always comes from a provider, never from a model."""

    title: str
    url: str
    snippet: str = ""
    provider: str = ""


async def search_learn(query: str, limit: int = 8) -> list[SearchHit]:
    params = {"search": query, "locale": "en-us", "$top": limit}
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.get(LEARN_URL, params=params)
        response.raise_for_status()
        results = response.json().get("results", [])
    return [
        SearchHit(
            title=hit.get("title") or "",
            url=hit.get("url") or "",
            snippet=hit.get("description") or "",
            provider="learn",
        )
        for hit in results
        if hit.get("url")
    ]


def _github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = get_settings().github_token
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _github_pass(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
    response = await client.get(
        GITHUB_URL, params={"q": query, "per_page": limit}, headers=_github_headers()
    )
    if response.status_code >= 400:
        # Unauthenticated search allows ~10 requests a minute and we have hit it. Degrading to
        # the other providers beats failing the job.
        logger.warning("github search returned %s for %r", response.status_code, query)
        return []
    return [
        SearchHit(
            title=item["full_name"],
            url=item["html_url"],
            snippet=(item.get("description") or "")[:200],
            provider="github",
        )
        for item in response.json().get("items", [])
    ]


async def search_github(query: str, limit: int = 4) -> list[SearchHit]:
    """Scoped pass first, because GitHub ranks on README text.

    Measured: best-match and `sort=stars` both omit `microsoft/agent-framework` entirely while
    `in:name,description` ranks it first, so sample repos bury the canonical repository. The
    concatenation order decides which pass wins de-duplication.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        named = await _github_pass(client, f"{query} in:name,description", limit)
        broad = await _github_pass(client, query, limit)
    return named + broad


@lru_cache
def get_search_agent() -> Agent:
    client = get_chat_client()
    return client.as_agent(
        name=SEARCH_AGENT_NAME,
        instructions=SEARCH_AGENT_INSTRUCTIONS,
        tools=[client.get_web_search_tool()],
    )


def _annotation_field(annotation: object, field: str) -> str | None:
    """Annotations arrive as plain dicts. Reading them as attributes returns None for every
    citation and yields zero hits with no error, so both shapes are handled."""
    if isinstance(annotation, dict):
        return annotation.get(field)
    return getattr(annotation, field, None)


def url_citations(response: object) -> list[SearchHit]:
    """Keep the citations and discard the prose — the URLs come from the tool, so a URL the
    model could mistype is unrepresentable."""
    found: list[SearchHit] = []
    for message in getattr(response, "messages", None) or []:
        for content in getattr(message, "contents", None) or []:
            for annotation in getattr(content, "annotations", None) or []:
                url = _annotation_field(annotation, "url")
                if url:
                    found.append(
                        SearchHit(
                            title=_annotation_field(annotation, "title") or url,
                            url=url,
                            provider="generic",
                        )
                    )
    return found


async def search_generic(query: str, limit: int = 8) -> list[SearchHit]:
    """The discovery provider. It reaches first-party sites our two APIs cannot — rust-lang.org,
    react.dev, spark.apache.org — which is what a first search has to be able to find.

    ⚠️ The question stays neutral about what kind of thing the subject is. Asking for "the
    project's own site or repository" presumed one existed and manufactured a software namesake
    for anything that was not software: measured, it returned the GUITAR testing framework for
    "Guitar" and Python's `statistics` module for "Statistics", while the neutral question
    returned Britannica and Stanford. It costs the technical cases nothing — Python and Rust
    still resolve to the language, because that is genuinely the dominant meaning.
    """
    response = await get_search_agent().run(
        f"What is {query}? Cite whatever authoritative sources describe it, "
        "whatever kind of thing it turns out to be."
    )
    return dedupe(url_citations(response))[:limit]


# The only two domains whose API is precise enough to BE the domain restriction.
DomainAdapter = Callable[[str], Awaitable[list[SearchHit]]]
DOMAIN_ADAPTERS: dict[str, DomainAdapter] = {
    "learn.microsoft.com": search_learn,
    "github.com": search_github,
}


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def on_domains(hit: SearchHit, domains: Sequence[str]) -> bool:
    host = host_of(hit.url)
    return any(host == wanted or host.endswith(f".{wanted}") for wanted in (d.lower() for d in domains))


def dedupe(hits: Sequence[SearchHit]) -> list[SearchHit]:
    seen: set[str] = set()
    out: list[SearchHit] = []
    for hit in hits:
        key = hit.url.rstrip("/")
        if key and key not in seen:
            seen.add(key)
            out.append(hit)
    return out


def pick(hits: Sequence[SearchHit], numbers: Sequence[int], budget: int) -> list[SearchHit]:
    """Indexes into the list we supplied, never URLs, so a mistyped URL is unrepresentable.

    Out-of-range numbers are dropped rather than wrapped: a silent modulo would hand back a
    source the model never chose. Shared by both nodes that select sources, so the rule cannot
    drift into two versions.
    """
    chosen: list[SearchHit] = []
    for number in numbers:
        if 1 <= number <= len(hits) and hits[number - 1] not in chosen:
            chosen.append(hits[number - 1])
        if len(chosen) >= budget:
            break
    return chosen


async def search_web(query: str, domains: Sequence[str] | None = None) -> list[SearchHit]:
    if not domains:
        return await search_generic(query)

    adapters = [DOMAIN_ADAPTERS[domain] for domain in domains if domain in DOMAIN_ADAPTERS]
    if adapters:
        gathered = await asyncio.gather(*(call(query) for call in adapters), return_exceptions=True)
        hits: list[SearchHit] = []
        for result in gathered:
            if isinstance(result, BaseException):
                logger.warning("domain adapter failed for %r: %r", query, result)
            else:
                hits.extend(result)
        return dedupe(hits)

    # No adapter for these domains, so search generally and filter. An empty result is reported
    # as empty: handing back unfiltered hits would silently answer a question nobody asked.
    return dedupe([hit for hit in await search_generic(query) if on_domains(hit, domains)])
