"""Builds the Foundry chat client shared by every agent."""

from __future__ import annotations

from functools import lru_cache

from agent_framework_foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential

from backend.config.settings import get_settings


@lru_cache
def get_chat_client() -> FoundryChatClient:
    settings = get_settings()
    return FoundryChatClient(
        project_endpoint=settings.foundry_project_endpoint,
        model=settings.foundry_model_deployment_name,
        credential=DefaultAzureCredential(),
    )
