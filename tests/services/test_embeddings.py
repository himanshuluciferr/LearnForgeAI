"""Tests for the embedding calls behind the search index.

The width is the interesting part: the index field is built to a fixed size, and a vector of
any other length is rejected on upload. The setting existed for a while without the call ever
passing it, so the index and the embedder could disagree and nothing said so.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.config.settings import get_settings
from backend.services import embeddings


class StubEmbeddings:
    """Records what was asked for, and answers in a deliberately shuffled order."""

    def __init__(self, width: int = 8) -> None:
        self.calls: list[dict] = []
        self.width = width

    async def create(self, *, model: str, input: list[str], **rest):
        self.calls.append({"model": model, "input": input, **rest})
        data = [
            SimpleNamespace(index=position, embedding=[float(position)] * self.width)
            for position in range(len(input))
        ]
        # Out of order on purpose: the caller pairs vectors with passages by position, so
        # trusting the reply's order would attach the wrong vector to the text.
        return SimpleNamespace(data=list(reversed(data)))


@pytest.fixture
def client(monkeypatch) -> StubEmbeddings:
    stub = StubEmbeddings()
    monkeypatch.setattr(
        embeddings, "get_embedding_client", lambda: SimpleNamespace(embeddings=stub)
    )
    return stub


@pytest.mark.asyncio
async def test_the_width_the_index_expects_is_the_width_that_is_asked_for(client):
    await embeddings.embed(["a passage"])

    assert client.calls[0]["dimensions"] == get_settings().embedding_dimensions


@pytest.mark.asyncio
async def test_vectors_come_back_against_the_text_they_belong_to(client):
    """The reply is sorted by index rather than taken as it arrives."""
    vectors = await embeddings.embed(["first", "second", "third"])

    assert [vector[0] for vector in vectors] == [0.0, 1.0, 2.0]


@pytest.mark.asyncio
async def test_a_long_course_is_sent_in_batches(client):
    """One call per passage would be thousands of round trips for a single course."""
    vectors = await embeddings.embed([f"passage {n}" for n in range(embeddings.BATCH + 5)])

    assert len(client.calls) == 2
    assert len(vectors) == embeddings.BATCH + 5


@pytest.mark.asyncio
async def test_nothing_to_embed_is_not_a_round_trip(client):
    assert await embeddings.embed([]) == []
    assert client.calls == []


@pytest.mark.asyncio
async def test_a_short_reply_is_refused_rather_than_misaligned(monkeypatch):
    """Silently returning fewer vectors would pair passages with the wrong text from that
    point on, which is worse than failing."""

    class Short(StubEmbeddings):
        async def create(self, *, model: str, input: list[str], **rest):
            reply = await super().create(model=model, input=input, **rest)
            return SimpleNamespace(data=reply.data[:-1])

    monkeypatch.setattr(
        embeddings, "get_embedding_client", lambda: SimpleNamespace(embeddings=Short())
    )

    with pytest.raises(ValueError, match="asked for 3 vectors"):
        await embeddings.embed(["a", "b", "c"])


def test_the_index_is_built_to_the_same_width_the_embedder_produces(monkeypatch):
    """Two settings that must agree. Vectors are only in the index when a deployment is
    configured, so the test says so itself rather than depending on a developer's .env - the
    first version of this passed locally and failed everywhere else."""
    from backend.services import ai_search  # noqa: PLC0415

    monkeypatch.setattr(ai_search, "vectors_enabled", lambda: True)
    fields = {field.name: field for field in ai_search.index_definition().fields}

    assert fields["vector"].vector_search_dimensions == get_settings().embedding_dimensions
