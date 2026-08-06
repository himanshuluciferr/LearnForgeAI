"""Entry point for the research skill: proves a proposed source exists and is on topic."""

from __future__ import annotations

import asyncio
import logging
import re
from ipaddress import ip_address
from urllib.parse import urlparse

import httpx

from backend.workflow.state import ResearchSource

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 8.0
USER_AGENT = "LearnForgeAI/1.0 (+course generator source check)"

BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}
BLOCKED_SUFFIXES = (".internal", ".local", ".localhost")

TAG = re.compile(r"<[^>]*>")
NON_WORD = re.compile(r"[^a-z0-9]+")
# Enough of a page to tell what it is about. The model chooses these URLs, so we must never
# agree to download whatever they happen to point at.
MAX_CHARS = 200_000
# Publishers drop their own name in their own documentation.
VENDORS = frozenset(
    {"amazon", "apache", "aws", "azure", "google", "ibm", "meta", "microsoft", "openai", "oracle"}
)


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


def phrase(text: str) -> str:
    """Case-folded words separated by single spaces, padded so matches land on word edges.

    Markup is dropped first, otherwise a tag between two words hides the phrase spanning them.
    """
    return f" {NON_WORD.sub(' ', TAG.sub(' ', text).casefold()).strip()} "


def wanted_phrases(skill: str) -> tuple[str, ...]:
    """The skill as the learner wrote it, plus the form its own documentation uses.

    The official page for Microsoft Agent Framework is titled "Agent Framework documentation"
    and never spells out the vendor, so demanding the full name would reject the best source.
    Two words must survive the trim, or a skill like "Azure Functions" would match on "functions".
    """
    full = phrase(skill)
    words = full.split()
    if len(words) > 2 and words[0] in VENDORS:
        return full, f" {' '.join(words[1:])} "
    return (full,)


async def read_page(client: httpx.AsyncClient, url: str) -> str:
    """Streams the start of a page. Never holds more than MAX_CHARS of an untrusted URL."""
    chunks: list[str] = []
    length = 0
    async with client.stream("GET", url) as response:
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {response.status_code}", request=response.request, response=response
            )
        async for chunk in response.aiter_text():
            chunks.append(chunk)
            length += len(chunk)
            if length >= MAX_CHARS:
                break
    # A single chunk can overshoot the cap, so the ceiling is enforced here rather than assumed.
    return "".join(chunks)[:MAX_CHARS]


async def inspect_source(
    client: httpx.AsyncClient, source: ResearchSource, wanted: tuple[str, ...]
) -> bool:
    """Records whether the page names the skill. Returns False when the link should be dropped."""
    try:
        page = await read_page(client, source.url)
    except httpx.HTTPError as exc:
        logger.info("Dropping source %s: %s", source.url, exc)
        return False

    text = phrase(page)
    source.mentions_skill = any(name in text for name in wanted)
    return True


async def verify_sources(sources: list[ResearchSource], skill: str) -> list[ResearchSource]:
    """Drops invented or dead links, and records which pages actually name the skill.

    A 200 proves the page exists, never that it is about the subject the learner asked for.
    That gap is how a model that has not heard of a skill quietly researches a different one.
    """
    candidates = [source for source in sources if is_fetchable(source.url)]
    if not candidates:
        return []

    wanted = wanted_phrases(skill)
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
        headers={"user-agent": USER_AGENT},
    ) as client:
        verdicts = await asyncio.gather(
            *(inspect_source(client, source, wanted) for source in candidates)
        )

    return [source for source, alive in zip(candidates, verdicts) if alive]
