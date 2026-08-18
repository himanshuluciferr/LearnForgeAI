"""Microsoft Learn documentation, read over MCP.

A quality upgrade on top of general web search, not a replacement for it: this server returns
the documentation TEXT rather than links to it, in deep-page chunks that a keyword search
never surfaces. It is free, keyless, and covers only Microsoft's own corpus — which is why
callers must establish that the subject is a Microsoft one before asking.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import OrderedDict

from agent_framework import MCPStreamableHTTPTool

from backend.workflow.state import SourceDocument

logger = logging.getLogger(__name__)

SERVER_URL = "https://learn.microsoft.com/api/mcp"
SEARCH_TOOL = "microsoft_docs_search"
CODE_TOOL = "microsoft_code_sample_search"

# The server answers in <=500-token chunks, so one page arrives as several results.
MAX_DOCUMENTS = 20
MAX_CHARS_PER_DOCUMENT = 60_000
TIMEOUT_SECONDS = 60.0

# A page that returned less than about one full chunk is a stub, and it costs a source slot
# that a real page could use. Measured on run 7: the pages retrieved were 160/164/166/195/
# 247/255/269 words or 368+, with nothing in between.
MIN_PAGE_WORDS = 300

# Code samples are filtered by language on the server, so they are the one part of the Learn
# corpus that cannot skew a course towards another language.
MAX_CODE_SAMPLES = 12
MIN_SNIPPET_CHARS = 120

# Learn publishes one page per language and distinguishes them ONLY in the title: both
# variants carry the same contentUrl, so grouping by url alone merges C# and Python prose
# into one document and the course switches language between topics.
VARIANT = re.compile(r"\(programming-language-([a-z0-9+#-]+)\)$")

# The learner says "C#" or ".NET"; Learn says "csharp".
ALIASES = {
    "c#": "csharp",
    "c sharp": "csharp",
    ".net": "csharp",
    "dotnet": "csharp",
    "py": "python",
    "ts": "typescript",
    "js": "javascript",
    "node": "javascript",
}


def normalise_language(name: str) -> str:
    cleaned = name.strip().lower()
    return ALIASES.get(cleaned, cleaned)


def split_title(title: str) -> tuple[str, str]:
    """Returns the readable title and the language it was written for.

    The suffix is stripped because the title rides into every downstream prompt, where
    "(programming-language-csharp)" is noise the writer does not need.
    """
    cleaned = title.strip()
    match = VARIANT.search(cleaned)
    if not match:
        return cleaned, ""
    return VARIANT.sub("", cleaned).strip(), normalise_language(match.group(1))


def _documents(chunks: object) -> list[dict]:
    """Each chunk carries its OWN json document, so joining them first is a parse error.

    Chunks also arrive duplicated, which silently doubled a retrieval measurement before it
    was caught, so callers must de-duplicate what comes out of here.
    """
    parsed: list[dict] = []
    for chunk in chunks or []:  # type: ignore[union-attr]
        text = getattr(chunk, "text", "") or ""
        if not text.strip():
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("learn-docs: a chunk was not json, skipping")
            continue
        if isinstance(payload, dict):
            parsed.append(payload)
    return parsed


def results_in(chunks: object) -> list[dict]:
    return [
        result
        for payload in _documents(chunks)
        for result in payload.get("results", [])
        if isinstance(result, dict)
    ]


def group_by_page(results: list[dict]) -> list[SourceDocument]:
    """One document per page AND language, its chunks joined in the order they arrived.

    Keyed on language as well as url because Learn serves both variants of a page from the
    same url, and merging them produces a document that teaches two languages at once.

    Identical chunks are dropped: the same passage comes back both across the duplicated
    chunk objects and across overlapping queries, and counting it twice would inflate the
    evidence budget that decides how many topics a course can afford.
    """
    pages: OrderedDict[tuple[str, str], dict] = OrderedDict()
    for result in results:
        url = (result.get("contentUrl") or "").split("#")[0].strip()
        content = (result.get("content") or "").strip()
        if not url or not content:
            continue
        title, language = split_title(result.get("title") or url)
        page = pages.setdefault(
            (url, language), {"title": title, "url": url, "seen": set(), "parts": []}
        )
        if content in page["seen"]:
            continue
        page["seen"].add(content)
        page["parts"].append(content)

    return [
        SourceDocument(
            title=str(page["title"]),
            url=str(page["url"]),
            text="\n\n".join(page["parts"])[:MAX_CHARS_PER_DOCUMENT],
            language=language,
        )
        for (_, language), page in pages.items()
    ]


async def _call(mcp: MCPStreamableHTTPTool, tool: str, **arguments) -> list[dict]:
    """A failing query narrows the evidence rather than failing the job."""
    try:
        return results_in(await mcp.call_tool(tool, **arguments))
    except Exception as error:  # the server is third-party and unauthenticated
        logger.warning("learn-docs: %s(%s) failed: %s", tool, arguments, error)
        return []


async def read_learn_docs(queries: list[str], limit: int = MAX_DOCUMENTS) -> list[SourceDocument]:
    """Search the Learn corpus for every query and return the pages behind the answers."""
    if not queries:
        return []

    try:
        async with MCPStreamableHTTPTool(name="learn-docs", url=SERVER_URL) as mcp:
            gathered = await asyncio.wait_for(
                asyncio.gather(
                    *(_call(mcp, SEARCH_TOOL, query=query) for query in queries)
                ),
                timeout=TIMEOUT_SECONDS * len(queries),
            )
    except Exception as error:
        logger.warning("learn-docs: server unreachable, continuing without it: %s", error)
        return []

    documents = group_by_page([result for results in gathered for result in results])
    substantial = [document for document in documents if document.words >= MIN_PAGE_WORDS]
    logger.info(
        "learn-docs: %d queries -> %d pages, %d substantial, %d chars",
        len(queries),
        len(documents),
        len(substantial),
        sum(len(document.text) for document in substantial),
    )
    return substantial[:limit]


def as_samples(results: list[dict], language: str) -> list[SourceDocument]:
    """One document per snippet, de-duplicated by the code itself.

    The same sample is returned under several queries, and a snippet short enough to be a
    single import line teaches nothing while still costing prompt budget.
    """
    documents: list[SourceDocument] = []
    seen: set[str] = set()
    for result in results:
        snippet = (result.get("codeSnippet") or "").strip()
        link = (result.get("link") or "").strip()
        if len(snippet) < MIN_SNIPPET_CHARS or snippet in seen or not link:
            continue
        seen.add(snippet)
        # The server prefixes the field with its own name, and the title rides into prompts.
        description = re.sub(r"^description:\s*", "", (result.get("description") or "").strip())
        documents.append(
            SourceDocument(
                title=description[:100] or "Code sample",
                url=link,
                text=f"{description}\n\n```{language}\n{snippet}\n```",
                language=language,
            )
        )
    return documents


async def read_code_samples(
    queries: list[str], language: str, limit: int = MAX_CODE_SAMPLES
) -> list[SourceDocument]:
    """Worked examples in one language, filtered by the server rather than by us.

    This is the only part of the Learn corpus that is language-correct by construction: the
    prose pages come back in C#, Go and Python for the same url, and filtering those client
    side is what cost a Python course two thirds of its documentation.
    """
    if not queries:
        return []

    wanted = normalise_language(language)
    try:
        async with MCPStreamableHTTPTool(name="learn-code", url=SERVER_URL) as mcp:
            gathered = await asyncio.wait_for(
                asyncio.gather(
                    *(
                        _call(mcp, CODE_TOOL, query=query, language=wanted)
                        for query in queries
                    )
                ),
                timeout=TIMEOUT_SECONDS * len(queries),
            )
    except Exception as error:
        logger.warning("learn-docs: code samples unavailable, continuing without them: %s", error)
        return []

    samples = as_samples([result for results in gathered for result in results], wanted)
    logger.info(
        "learn-docs: %d queries -> %d %s samples, %d chars",
        len(queries),
        len(samples),
        wanted,
        sum(len(sample.text) for sample in samples),
    )
    return samples[:limit]
