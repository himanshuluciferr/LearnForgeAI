"""The index policies have to cover what we ORDER BY.

Cosmos answers a query that orders by an excluded path with BadRequest: "The index path
corresponding to the specified order-by item is excluded." Nothing offline catches that,
because the local stores are dicts and files — so it only appeared against real Cosmos, in a
live run, after the endpoint had shipped. This is the cheap guard.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services import course_store, job_store

INFRA = Path(__file__).resolve().parents[2] / "infra" / "cosmos"


def included(policy_file: str) -> set[str]:
    policy = json.loads((INFRA / policy_file).read_text(encoding="utf-8"))
    return {path["path"] for path in policy["includedPaths"]}


@pytest.mark.parametrize(
    "policy_file, ordered",
    [
        ("jobs-index.json", job_store.ORDER_FIELD),
        ("courses-index.json", course_store.ORDER_FIELD),
    ],
)
def test_the_field_a_listing_orders_by_is_indexed(policy_file, ordered):
    assert f"/{ordered}/?" in included(policy_file)


@pytest.mark.parametrize("policy_file", ["jobs-index.json", "courses-index.json"])
def test_the_partition_key_is_indexed(policy_file):
    """Every listing is partition-scoped, so user_id has to be reachable by the index."""
    assert "/user_id/?" in included(policy_file)


@pytest.mark.parametrize("policy_file", ["jobs-index.json", "courses-index.json"])
def test_everything_else_stays_excluded(policy_file):
    """Courses embed the whole CourseState. Indexing all of it would index every word of
    every chapter."""
    policy = json.loads((INFRA / policy_file).read_text(encoding="utf-8"))

    assert {path["path"] for path in policy["excludedPaths"]} == {"/*"}
