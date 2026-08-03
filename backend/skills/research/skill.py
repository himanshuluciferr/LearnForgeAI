"""Entry point for the research skill: proves a proposed source actually exists."""

from __future__ import annotations

import asyncio
import logging
from ipaddress import ip_address
from urllib.parse import urlparse

import httpx

from backend.workflow.state import ResearchSource

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 8.0
USER_AGENT = "LearnForgeAI/1.0 (+course generator source check)"

BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}
BLOCKED_SUFFIXES = (".internal", ".local", ".localhost")


def is_fetchable(url: str) -> bool:
    """The model chooses these URLs, so they are untrusted input we must not blindly fetch."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False

    host = (parsed.hostname or "").lower()
    if not host or host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
        return False

    try:
        return ip_address(host).is_global
    except ValueError:
        return True  # a domain name rather than a literal address


async def is_reachable(client: httpx.AsyncClient, source: ResearchSource) -> bool:
    try:
        response = await client.head(source.url)
        if response.status_code == 405:  # some doc servers only answer GET
            response = await client.get(source.url)
    except httpx.HTTPError as exc:
        logger.info("Dropping source %s: %s", source.url, exc)
        return False

    if response.status_code >= 400:
        logger.info("Dropping source %s: HTTP %d", source.url, response.status_code)
        return False
    return True


async def verify_sources(sources: list[ResearchSource]) -> list[ResearchSource]:
    """Drops invented or dead links. A confident 404 is worse than no citation at all."""
    candidates = [source for source in sources if is_fetchable(source.url)]
    if not candidates:
        return []

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
        headers={"user-agent": USER_AGENT},
    ) as client:
        verdicts = await asyncio.gather(*(is_reachable(client, s) for s in candidates))

    return [source for source, alive in zip(candidates, verdicts) if alive]
