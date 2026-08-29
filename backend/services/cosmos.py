"""Cosmos DB access — users, courses, progress, quiz scores, chat history.

Keyless throughout: DefaultAzureCredential only, so no connection string ever reaches .env.
Note that the data plane has its own RBAC — an ARM "Owner" still gets 403 here until the
Cosmos DB Data Contributor role is assigned (scripts/provision_cosmos.ps1 does that).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from azure.cosmos.aio import ContainerProxy, CosmosClient
from azure.identity.aio import DefaultAzureCredential
from pydantic import BaseModel

from backend.config.settings import get_settings

JOBS = "jobs"
COURSES = "courses"
QUIZ_RESULTS = "quiz_results"
PROGRESS = "progress"
USERS = "users"


def to_document(model: BaseModel) -> dict[str, Any]:
    """mode="json" is required: Cosmos stores JSON, and the models hold datetimes and enums."""
    return model.model_dump(mode="json")


def cosmos_enabled() -> bool:
    return bool(get_settings().cosmos_endpoint)


@lru_cache
def _connection() -> tuple[CosmosClient, DefaultAzureCredential]:
    # The credential is kept alongside the client because closing the client does not close it.
    credential = DefaultAzureCredential()
    return CosmosClient(get_settings().cosmos_endpoint, credential=credential), credential


def get_container(name: str) -> ContainerProxy:
    """Proxies are local handles — nothing is sent to Cosmos until an item call is made."""
    client, _ = _connection()
    return client.get_database_client(get_settings().cosmos_database).get_container_client(name)


async def close_cosmos() -> None:
    """Both objects hold sockets that outlive a request, so the app lifespan closes them."""
    if not _connection.cache_info().currsize:
        return
    client, credential = _connection()
    await client.close()
    await credential.close()
    _connection.cache_clear()
