"""The index policies have to cover what we ORDER BY.

Cosmos answers a query that orders by an excluded path with BadRequest: "The index path
corresponding to the specified order-by item is excluded." Nothing offline sees it, because the
local stores are dicts and files — so it only appears against real Cosmos, in a live run, after
the endpoint has shipped. It happened twice: jobs ordered by updated_at, then quiz_results by
taken_at, because the first version of this file listed two stores when there were three.

So the coverage is checked too: a store that orders by something and is not listed here fails
the last test rather than production.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.services import course_store, job_store, quiz_store

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra" / "cosmos"
SERVICES = ROOT / "backend" / "services"
PROVISION = ROOT / "scripts" / "provision_cosmos.ps1"

# Store module -> (container, the field it orders by).
ORDERED = {
    "job_store": ("jobs", job_store.ORDER_FIELD),
    "course_store": ("courses", course_store.ORDER_FIELD),
    "quiz_store": ("quiz_results", quiz_store.ORDER_FIELD),
}


def policy_files() -> dict[str, str]:
    """Read the container-to-policy mapping out of the provisioning script, which is what
    creates them, rather than keeping a second copy here to drift."""
    script = PROVISION.read_text(encoding="utf-8")
    return dict(re.findall(r'Name\s*=\s*"([^"]+)".*?Index\s*=\s*"([^"]+)"', script))


def included(container: str) -> set[str]:
    policy = json.loads((INFRA / policy_files()[container]).read_text(encoding="utf-8"))
    return {path["path"] for path in policy["includedPaths"]}


@pytest.mark.parametrize("container, ordered", sorted(ORDERED.values()))
def test_the_field_a_listing_orders_by_is_indexed(container, ordered):
    assert f"/{ordered}/?" in included(container)


@pytest.mark.parametrize("container", sorted(c for c, _ in ORDERED.values()))
def test_the_partition_key_is_indexed(container):
    """Every listing is partition-scoped, so user_id has to be reachable by the index."""
    assert "/user_id/?" in included(container)


@pytest.mark.parametrize("container", sorted(c for c, _ in ORDERED.values()))
def test_everything_else_stays_excluded(container):
    """Courses embed the whole CourseState. Indexing all of it would index every word of
    every chapter."""
    policy = json.loads((INFRA / policy_files()[container]).read_text(encoding="utf-8"))

    assert {path["path"] for path in policy["excludedPaths"]} == {"/*"}


def test_every_container_the_script_creates_has_a_policy_on_disk():
    assert [name for name, file in policy_files().items() if not (INFRA / file).is_file()] == []


def test_every_store_that_orders_is_checked_here():
    """The hole that let the second failure through: this file listed two stores when there
    were three, so quiz_results was never looked at."""
    defining = {
        path.stem
        for path in SERVICES.glob("*.py")
        if re.search(r"^ORDER_FIELD\s*=", path.read_text(encoding="utf-8"), re.MULTILINE)
    }

    assert defining <= set(ORDERED), f"not covered here: {sorted(defining - set(ORDERED))}"
