"""Embeddings for the search index.

Its own module rather than part of ai_search: an index can be keyword-only, and a search
service and an embedding model are two separate things to have provisioned. Keeping them apart
means a missing embedding deployment degrades to keyword search rather than failing.

Goes to the same Foundry endpoint as every other model call, keyless.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AsyncAzureOpenAI

from backend.config.settings import get_settings

logger = logging.getLogger(__name__)

SCOPE = "https://cognitiveservices.azure.com/.default"
# One call per passage would be thousands of round trips for a course; the service takes a
# batch and this is a size it reliably accepts.
BATCH = 64


@lru_cache
def get_embedding_client() -> AsyncAzureOpenAI:
    settings = get_settings()
    # The project endpoint carries /api/projects/<name>; embeddings live on the account root.
    account = settings.foundry_project_endpoint.split("/api/projects/")[0]
    return AsyncAzureOpenAI(
        azure_endpoint=account,
        azure_ad_token_provider=get_bearer_token_provider(DefaultAzureCredential(), SCOPE),
        api_version="2024-10-21",
    )


async def embed(texts: list[str]) -> list[list[float]]:
    """Returns one vector per input, in the same order — the caller pairs them with passages
    by position, so a reordered or short reply would attach the wrong vector to the text."""
    if not texts:
        return []
    client = get_embedding_client()
    settings = get_settings()
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH):
        chunk = texts[start : start + BATCH]
        # Asked for explicitly: the index field is built to this width, and a vector of any
        # other length is rejected on upload. Without it the setting was decoration.
        response = await client.embeddings.create(
            model=settings.embedding_deployment,
            input=chunk,
            dimensions=settings.embedding_dimensions,
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors.extend(item.embedding for item in ordered)
    if len(vectors) != len(texts):
        raise ValueError(f"embeddings: asked for {len(texts)} vectors, got {len(vectors)}")
    return vectors


async def embed_one(text: str) -> list[float]:
    return (await embed([text]))[0]
