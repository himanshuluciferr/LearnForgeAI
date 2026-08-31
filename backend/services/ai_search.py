"""Azure AI Search over the passages of a course.

Keyless, like every other Azure client here: DefaultAzureCredential only, so no admin key
reaches .env. Reading and writing need the Search Index Data Contributor role on the service,
and control-plane Owner does not grant it — the same trap as Cosmos.

One document per passage rather than per course. A course is 160,000 characters and a question
wants a paragraph, so indexing whole courses would return the whole course.

Hybrid rather than vector alone: the measured strength of the lexical selector was exact
identifiers — `ownerReference`, `--rebase-merges` — which is what keyword matching is good at
and what embeddings blur. Vector covers the paraphrase keyword misses. Neither replaces the
other.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Iterable

from azure.core.exceptions import ResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery

from backend.config.settings import get_settings

logger = logging.getLogger(__name__)

PROFILE = "passage-vector"
ALGORITHM = "passage-hnsw"

# A question wants a paragraph. Beyond this the answer is buried in what surrounds it.
TOP_K = 12


def search_enabled() -> bool:
    return bool(get_settings().search_endpoint)


def vectors_enabled() -> bool:
    """Keyword-only is a valid index. Vectors cost an embedding call per passage and per
    question, and need a second model deployed."""
    return bool(get_settings().embedding_deployment)


@lru_cache
def _credential() -> DefaultAzureCredential:
    return DefaultAzureCredential()


@lru_cache
def get_search_client() -> SearchClient:
    """One per process, like the Cosmos client. A fresh one per query spends a TLS handshake
    before it can ask anything: measured at 4.4s a question against 286ms on a warm client,
    which is most of what a mentor answer was waiting for.

    Callers must not close it. `close_search` does that once, at shutdown.
    """
    settings = get_settings()
    return SearchClient(settings.search_endpoint, settings.search_index, _credential())


def get_index_client() -> SearchIndexClient:
    return SearchIndexClient(get_settings().search_endpoint, _credential())


def escape(value: str) -> str:
    """An OData string literal escapes a quote by doubling it. Without this an id containing
    one would change the filter rather than be matched by it."""
    return value.replace("'", "''")


def owned_by(course_id: str, user_id: str) -> str:
    """Not a nicety: without it the index is one shared corpus and a question could be
    answered out of somebody else's course."""
    return f"course_id eq '{escape(course_id)}' and user_id eq '{escape(user_id)}'"


def index_definition() -> SearchIndex:
    settings = get_settings()
    fields: list[Any] = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="user_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="course_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="chapter_number", type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(name="url", type=SearchFieldDataType.String),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SearchableField(name="text", type=SearchFieldDataType.String),
    ]
    vector_search = None
    if vectors_enabled():
        fields.append(
            SearchField(
                name="vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=settings.embedding_dimensions,
                vector_search_profile_name=PROFILE,
            )
        )
        vector_search = VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name=ALGORITHM)],
            profiles=[VectorSearchProfile(name=PROFILE, algorithm_configuration_name=ALGORITHM)],
        )
    return SearchIndex(name=settings.search_index, fields=fields, vector_search=vector_search)


async def ensure_index() -> None:
    """Created on demand rather than by the provisioning script, because its shape follows the
    code that writes to it and the two drifting apart is a silent wrong-results bug."""
    client = get_index_client()
    async with client:
        try:
            await client.get_index(get_settings().search_index)
            return
        except ResourceNotFoundError:
            pass
        await client.create_index(index_definition())
        logger.info("ai-search: created index %s", get_settings().search_index)


async def upload(documents: Iterable[dict[str, Any]], batch: int = 500) -> int:
    """Batched because the service rejects a payload beyond about 16 MB, and one course of
    passages carrying vectors is comfortably past it."""
    rows = list(documents)
    if not rows:
        return 0
    client = get_search_client()
    for start in range(0, len(rows), batch):
        await client.merge_or_upload_documents(rows[start : start + batch])
    return len(rows)


async def drop_course(course_id: str, user_id: str) -> int:
    """A regenerated course keeps its id, so its old passages would otherwise stay searchable
    alongside the new ones."""
    client = get_search_client()
    results = await client.search(
        search_text="*", filter=owned_by(course_id, user_id), select=["id"], top=1000
    )
    found = [{"id": row["id"]} async for row in results]
    if found:
        await client.delete_documents(found)
    return len(found)


async def search_passages(
    question: str,
    course_id: str,
    user_id: str,
    vector: list[float] | None = None,
    top: int = TOP_K,
) -> list[dict[str, Any]]:
    """Hybrid when a vector is supplied, keyword when it is not."""
    client = get_search_client()
    results = await client.search(
        search_text=question,
        filter=owned_by(course_id, user_id),
        vector_queries=(
            [VectorizedQuery(vector=vector, k_nearest_neighbors=top, fields="vector")]
            if vector
            else None
        ),
        select=["id", "title", "url", "text", "chapter_number"],
        top=top,
    )
    return [dict(row) async for row in results]


async def close_search() -> None:
    if get_search_client.cache_info().currsize:
        await get_search_client().close()
        get_search_client.cache_clear()
    if _credential.cache_info().currsize:
        await _credential().close()
        _credential.cache_clear()
