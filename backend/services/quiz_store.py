"""Where quiz attempts are kept. Interface-first, so Cosmos is a drop-in for the local store."""

from __future__ import annotations

import asyncio
from typing import Protocol

from backend.models.quiz import QuizAttempt
from backend.services.cosmos import QUIZ_RESULTS, cosmos_enabled, get_container, to_document

# Cosmos refuses to ORDER BY a path its index policy excludes, and quiz_results shares the jobs
# policy. Named so a test can check the policy still covers it.
ORDER_FIELD = "created_at"


class QuizStore(Protocol):
    async def save(self, attempt: QuizAttempt) -> QuizAttempt: ...

    async def for_course(self, course_id: str, user_id: str) -> list[QuizAttempt]: ...


class InMemoryQuizStore:
    def __init__(self) -> None:
        self._attempts: list[QuizAttempt] = []
        self._lock = asyncio.Lock()

    async def save(self, attempt: QuizAttempt) -> QuizAttempt:
        async with self._lock:
            self._attempts.append(attempt)
        return attempt

    async def for_course(self, course_id: str, user_id: str) -> list[QuizAttempt]:
        async with self._lock:
            return [
                attempt
                for attempt in self._attempts
                if attempt.course_id == course_id and attempt.user_id == user_id
            ]


class CosmosQuizStore:
    async def save(self, attempt: QuizAttempt) -> QuizAttempt:
        container = get_container(QUIZ_RESULTS)
        await container.upsert_item(to_document(attempt))
        return attempt

    async def for_course(self, course_id: str, user_id: str) -> list[QuizAttempt]:
        container = get_container(QUIZ_RESULTS)
        # user_id is the partition key, so this never crosses partitions.
        found = [
            item
            async for item in container.query_items(
                f"SELECT * FROM c WHERE c.course_id = @course ORDER BY c.{ORDER_FIELD} DESC",
                parameters=[{"name": "@course", "value": course_id}],
                partition_key=user_id,
            )
        ]
        return [QuizAttempt.model_validate(item) for item in found]


quiz_store: QuizStore = CosmosQuizStore() if cosmos_enabled() else InMemoryQuizStore()
