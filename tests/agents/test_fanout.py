"""Offline tests for the shared per-chapter fan-out."""

import asyncio
from dataclasses import dataclass

import pytest

from backend.agents.fanout import BASE_DELAY_SECONDS, MAX_ATTEMPTS, backoff, per_chapter


@dataclass
class Item:
    number: int


@pytest.mark.asyncio
async def test_results_come_back_in_chapter_order_not_completion_order():
    """Chapter numbering depends on this: the slowest chapter must not end up last."""
    items = [Item(1), Item(2), Item(3)]

    async def write_one(item: Item) -> int:
        await asyncio.sleep(0.03 if item.number == 1 else 0.0)
        return item.number

    assert await per_chapter("x-agent", items, write_one, 4) == [1, 2, 3]


@pytest.mark.asyncio
async def test_no_more_than_the_limit_run_at_once():
    in_flight = 0
    peak = 0

    async def write_one(item: Item) -> int:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.01)
            return item.number
        finally:
            in_flight -= 1

    await per_chapter("x-agent", [Item(n) for n in range(1, 10)], write_one, 3)

    assert peak == 3


@pytest.mark.asyncio
async def test_failures_are_collected_rather_than_cancelling_the_rest():
    started: list[int] = []

    async def write_one(item: Item) -> int:
        started.append(item.number)
        if item.number == 2:
            raise RuntimeError("boom")
        return item.number

    items = [Item(n) for n in range(1, 5)]

    with pytest.raises(ValueError, match=r"x-agent failed on chapters \[2\]"):
        await per_chapter("x-agent", items, write_one, 4)

    # Chapter 2 appears more than once now that it is retried, so only the reach matters.
    assert sorted(set(started)) == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_every_failure_is_named_not_just_the_first():
    async def write_one(item: Item) -> int:
        if item.number in (2, 4):
            raise RuntimeError("boom")
        return item.number

    items = [Item(n) for n in range(1, 5)]

    with pytest.raises(ValueError, match=r"failed on chapters \[2, 4\]"):
        await per_chapter("x-agent", items, write_one, 4)


@pytest.mark.asyncio
async def test_no_chapters_means_no_calls_and_no_failure():
    async def write_one(item: Item) -> int:
        raise AssertionError("should not be called")

    assert await per_chapter("x-agent", [], write_one, 4) == []


@pytest.mark.asyncio
async def test_a_transient_failure_is_retried_and_the_chapter_still_lands():
    """The whole point: one 429 must not cost the entire step."""
    calls = 0

    async def write_one(item: Item) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("429 Too Many Requests")
        return item.number

    assert await per_chapter("x-agent", [Item(1)], write_one, 4) == [1]
    assert calls == 2


@pytest.mark.asyncio
async def test_retrying_gives_up_rather_than_looping_forever():
    calls = 0

    async def write_one(item: Item) -> int:
        nonlocal calls
        calls += 1
        raise RuntimeError("still down")

    items = [Item(1)]

    with pytest.raises(ValueError, match=r"failed on chapters \[1\]"):
        await per_chapter("x-agent", items, write_one, 4)

    assert calls == MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_a_bug_in_our_own_code_is_not_retried():
    """A TypeError will fail identically three times; retrying only burns time and tokens."""
    calls = 0

    async def write_one(item: Item) -> int:
        nonlocal calls
        calls += 1
        raise TypeError("wrong shape")

    items = [Item(1)]

    with pytest.raises(ValueError, match=r"failed on chapters \[1\]"):
        await per_chapter("x-agent", items, write_one, 4)

    assert calls == 1


@pytest.mark.asyncio
async def test_a_retry_waits_outside_the_semaphore():
    """A chapter that is waiting for a slot must not queue behind another chapter's backoff.

    With the gate held across the sleep the order would be 1.1, 1.2, 2.1 instead.
    """
    starts: list[str] = []
    attempts: dict[int, int] = {}

    async def write_one(item: Item) -> int:
        attempts[item.number] = attempts.get(item.number, 0) + 1
        starts.append(f"{item.number}.{attempts[item.number]}")
        if item.number == 1 and attempts[1] == 1:
            raise RuntimeError("429")
        return item.number

    await per_chapter("x-agent", [Item(1), Item(2)], write_one, 1)

    assert starts == ["1.1", "2.1", "1.2"]


def test_backoff_grows_but_stays_inside_its_ceiling():
    for attempt in range(1, MAX_ATTEMPTS + 1):
        ceiling = BASE_DELAY_SECONDS * 2 ** (attempt - 1)
        assert ceiling / 2 <= backoff(attempt) <= ceiling


def test_backoff_is_jittered_so_throttled_chapters_do_not_wake_together():
    assert len({backoff(1) for _ in range(20)}) > 1
