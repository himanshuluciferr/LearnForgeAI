"""Shared fan-out for the steps that make one model call per chapter.

Extracted once chapter, practice and quiz all needed it, so the shape is drawn from three
real callers rather than guessed from one.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, TypeVar

logger = logging.getLogger(__name__)

# One transient 429 in a 20-chapter course would otherwise lose the whole step.
MAX_ATTEMPTS = 3
BASE_DELAY_SECONDS = 2.0

# Bugs in our own code fail identically every time, so retrying only burns time and tokens.
PERMANENT = (TypeError, AttributeError, KeyError, IndexError, NameError)


class Numbered(Protocol):
    """Anything a per-chapter step iterates: a ChapterOutline or a written Chapter."""

    number: int


ItemT = TypeVar("ItemT", bound=Numbered)
ResultT = TypeVar("ResultT")


def backoff(attempt: int) -> float:
    """Seconds to wait after a failed attempt.

    Jittered because the chapters run concurrently: without it, every chapter throttled by
    the same 429 would wake up together and re-create the burst that caused it.
    """
    ceiling = BASE_DELAY_SECONDS * 2 ** (attempt - 1)
    return ceiling * (0.5 + random.random() / 2)  # NOSONAR - spreads load, not a secret


async def with_retry(
    agent_name: str,
    item: ItemT,
    run: Callable[[ItemT], Awaitable[ResultT]],
) -> ResultT:
    """Call run(item), retrying transient failures with backoff."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await run(item)
        except PERMANENT:
            raise
        except Exception as error:  # CancelledError is a BaseException, so it still propagates.
            last_error = error
            if attempt == MAX_ATTEMPTS:
                break
            delay = backoff(attempt)
            logger.warning(
                "%s: chapter %d attempt %d/%d failed, retrying in %.1fs: %s",
                agent_name,
                item.number,
                attempt,
                MAX_ATTEMPTS,
                delay,
                error,
            )
            await asyncio.sleep(delay)
    raise last_error


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

    async def attempt_once(item: ItemT) -> ResultT:
        # The gate is taken per attempt, so a waiting chapter never queues behind a retry sleep.
        async with gate:
            return await write_one(item)

    logger.info("%s: running over %d chapters", agent_name, len(items))
    results = await asyncio.gather(
        *(with_retry(agent_name, item, attempt_once) for item in items),
        return_exceptions=True,
    )

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
