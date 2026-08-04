"""Offline tests for the shared per-chapter fan-out."""

import asyncio
from dataclasses import dataclass

import pytest

from backend.agents.fanout import per_chapter


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

    with pytest.raises(ValueError, match=r"x-agent failed on chapters \[2\]"):
        await per_chapter("x-agent", [Item(n) for n in range(1, 5)], write_one, 4)

    assert started == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_every_failure_is_named_not_just_the_first():
    async def write_one(item: Item) -> int:
        if item.number in (2, 4):
            raise RuntimeError("boom")
        return item.number

    with pytest.raises(ValueError, match=r"failed on chapters \[2, 4\]"):
        await per_chapter("x-agent", [Item(n) for n in range(1, 5)], write_one, 4)


@pytest.mark.asyncio
async def test_no_chapters_means_no_calls_and_no_failure():
    async def write_one(item: Item) -> int:
        raise AssertionError("should not be called")

    assert await per_chapter("x-agent", [], write_one, 4) == []
