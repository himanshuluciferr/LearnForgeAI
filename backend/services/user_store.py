"""Where registered learners are kept. Interface-first, so Cosmos is a drop-in for local runs."""

from __future__ import annotations

import asyncio
from typing import Protocol

from azure.cosmos.exceptions import CosmosResourceExistsError, CosmosResourceNotFoundError

from backend.models.user import User
from backend.services.cosmos import USERS, cosmos_enabled, get_container, to_document


class UserStore(Protocol):
    async def get(self, user_id: str) -> User | None: ...

    async def create(self, user: User) -> User | None: ...


class InMemoryUserStore:
    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._lock = asyncio.Lock()

    async def get(self, user_id: str) -> User | None:
        async with self._lock:
            return self._users.get(user_id)

    async def create(self, user: User) -> User | None:
        async with self._lock:
            if user.user_id in self._users:
                return None
            self._users[user.user_id] = user
        return user


class CosmosUserStore:
    async def get(self, user_id: str) -> User | None:
        try:
            document = await get_container(USERS).read_item(user_id, partition_key=user_id)
        except CosmosResourceNotFoundError:
            return None
        return User.model_validate(document)

    async def create(self, user: User) -> User | None:
        """create rather than upsert, and None on conflict: signing up twice must not quietly
        replace the first account's password."""
        try:
            await get_container(USERS).create_item(to_document(user))
        except CosmosResourceExistsError:
            return None
        return user


user_store: UserStore = CosmosUserStore() if cosmos_enabled() else InMemoryUserStore()
