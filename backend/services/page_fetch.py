"""Fetches and extracts the text of pages a search engine chose.

Finding needs providers; fetching does not — one path serves every URL.
"""

from __future__ import annotations

import asyncio
import logging
import re
from ipaddress import ip_address
from typing import Sequence
from urllib.parse import urlparse

import httpx

from backend.services.web_search import SearchHit, github_headers
from backend.workflow.state import SourceDocument

logger = logging.getLogger(__name__)

MAX_HTML_CHARS = 400_000
MAX_TEXT_CHARS = 40_000
# Below this a page is nav furniture rather than content — a landing page yielded 136 words.
MIN_WORDS = 50
TIMEOUT_SECONDS = 25.0

BLOCKED_SUFFIXES = ("localhost", ".internal", ".local")

# A /tree/ URL renders a directory listing, so fetching it as HTML yields file NAMES. A run on
# Microsoft Agent Framework pulled two sample folders and got 515 and 588 words of listing while
# the code the course needed sat one level down — and the chapters then invented an API.
GITHUB_TREE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+?)/?$")
GITHUB_BLOB = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$")
GITHUB_CONTENTS = "https://api.github.com/repos/{owner}/{repo}/contents/{path}"

# Worth reading as teaching material; everything else in a sample folder is build furniture.
CODE_SUFFIXES = (".py", ".cs", ".ts", ".js", ".java", ".go", ".rs", ".md", ".yaml", ".yml")
MAX_FILES_PER_FOLDER = 8

# Wikimedia 403s a vague contact string on both its API and REST endpoints and returns 200 as
# soon as the User-Agent carries a contact URL. Set in one place: fixing it in the search
# provider and leaving the fetcher behind made every Wikipedia read fail silently.
USER_AGENT = "LearnForgeAI/0.1 (+https://github.com/learnforge; course research)"


def is_fetchable(url: str) -> bool:
    """SSRF boundary. We are about to fetch pages a search engine chose, and
    DefaultAzureCredential itself probes 169.254.169.254 — the address this blocks."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host.endswith(BLOCKED_SUFFIXES):
        return False
    try:
        return ip_address(host).is_global
    except ValueError:
        return True


# Removed WITH their contents and first: stripping tags alone leaves nav and script text behind.
STRIP_BLOCKS = re.compile(r"<(script|style|nav|footer|header|svg)\b.*?</\1>", re.S | re.I)
TAGS = re.compile(r"<[^>]+>")
ENTITIES = re.compile(r"&[a-zA-Z#0-9]+;")
WHITESPACE = re.compile(r"\s+")


def extract_text(html: str) -> str:
    text = STRIP_BLOCKS.sub(" ", html)
    text = TAGS.sub(" ", text)
    text = ENTITIES.sub(" ", text)
    return WHITESPACE.sub(" ", text).strip()[:MAX_TEXT_CHARS]


async def _get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response | None:
    try:
        response = await client.get(url, **kwargs)
    except Exception as error:
        logger.info("fetch failed for %s: %s", url, type(error).__name__)
        return None
    # An explicit check, not raise_for_status: that raises on 3xx too, and a followed redirect
    # is the normal case for every Learn link.
    if response.status_code >= 400:
        logger.info("fetch returned %s for %s", response.status_code, url)
        return None
    return response


async def _read_folder(client: httpx.AsyncClient, match: re.Match[str]) -> str:
    """Reads the code inside a GitHub folder rather than the names of the files in it."""
    owner, repo, ref, path = match.groups()
    listing = await _get(
        client,
        GITHUB_CONTENTS.format(owner=owner, repo=repo, path=path),
        params={"ref": ref},
        headers=github_headers(),
    )
    if listing is None:
        return ""
    try:
        entries = listing.json()
    except ValueError:
        return ""
    if not isinstance(entries, list):
        return ""

    downloads = [
        (str(entry["name"]), str(entry["download_url"]))
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("type") == "file"
        and str(entry.get("name", "")).endswith(CODE_SUFFIXES)
        # The API supplies this URL, so it is still untrusted input to our own fetcher.
        and is_fetchable(str(entry.get("download_url") or ""))
    ][:MAX_FILES_PER_FOLDER]

    files = await asyncio.gather(*(_get(client, url) for _, url in downloads))
    return "\n\n".join(
        f"{name}\n{response.text}"
        for (name, _), response in zip(downloads, files)
        if response is not None
    )[:MAX_TEXT_CHARS]


async def _read(client: httpx.AsyncClient, url: str) -> str:
    folder = GITHUB_TREE.match(url)
    if folder:
        return await _read_folder(client, folder)

    source_file = GITHUB_BLOB.match(url)
    if source_file:
        owner, repo, ref, path = source_file.groups()
        raw = await _get(client, f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}")
        # Code is already plain text; running the HTML stripper over it would eat the syntax.
        return raw.text[:MAX_TEXT_CHARS] if raw is not None else ""

    response = await _get(client, url)
    return extract_text(response.text[:MAX_HTML_CHARS]) if response is not None else ""


async def _fetch_one(client: httpx.AsyncClient, hit: SearchHit) -> SourceDocument | None:
    text = await _read(client, hit.url)
    if not text:
        return None
    return SourceDocument(title=hit.title, url=hit.url, text=text)


async def fetch_documents(hits: Sequence[SearchHit], limit: int) -> list[SourceDocument]:
    usable = [hit for hit in hits if is_fetchable(hit.url)][:limit]
    if not usable:
        return []
    async with httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS,
        # Required: every Learn URL 302s to its /en-us/ form and would otherwise be dropped.
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        fetched = await asyncio.gather(*(_fetch_one(client, hit) for hit in usable))
    return [doc for doc in fetched if doc is not None and doc.words > MIN_WORDS]
