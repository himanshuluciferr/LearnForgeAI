"""Shared fan-out for the steps that make one model call per chapter.

Extracted once chapter, practice and quiz all needed it, so the shape is drawn from three
real callers rather than guessed from one.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, TypeVar

logger = logging.getLogger(__name__)


class Numbered(Protocol):
    """Anything a per-chapter step iterates: a ChapterOutline or a written Chapter."""

    number: int


ItemT = TypeVar("ItemT", bound=Numbered)
ResultT = TypeVar("ResultT")


async def per_chapter(
    agent_name: str,
    items: Sequence[ItemT],
    write_one: Callable[[ItemT], Awaitable[ResultT]],
    limit: int,
) -> list[ResultT]:
    """Run write_one for every chapter, at most `limit` at a time, all or nothing.

    Concurrency here is safe because each call returns its own object and nothing shared is
    mutated — unlike a workflow fan-out, where several executors would write one CourseState.
    """
    gate = asyncio.Semaphore(limit)

    async def guarded(item: ItemT) -> ResultT:
        async with gate:
            return await write_one(item)

    logger.info("%s: running over %d chapters", agent_name, len(items))
    results = await asyncio.gather(*(guarded(item) for item in items), return_exceptions=True)

    done: list[ResultT] = []
    failed: list[int] = []
    for item, result in zip(items, results):
        if isinstance(result, BaseException):
            logger.error("%s: chapter %d failed: %s", agent_name, item.number, result)
            failed.append(item.number)
        else:
            done.append(result)

    # A course with a hole in it still reads as finished, so a partial result is refused.
    if failed:
        raise ValueError(f"{agent_name} failed on chapters {failed}")

    return done
