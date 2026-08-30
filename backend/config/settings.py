"""Environment-backed settings loaded once at startup."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # override=False equivalent: real env vars win over .env, so Foundry runtime values take precedence.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"

    foundry_project_endpoint: str = ""
    foundry_model_deployment_name: str = "gpt-5-mini"

    # Unauthenticated GitHub search allows ~10 requests a minute and we have hit that ceiling.
    github_token: str = ""

    # Empty endpoint means local development, which keeps the in-memory and file stores.
    cosmos_endpoint: str = ""
    cosmos_database: str = "learnforge"

    # Empty means the publisher renders the course but writes it to generated_courses/
    # instead of uploading it, so local runs need no storage account.
    blob_account_url: str = ""

    # Empty means retrieval stays lexical, which is what the offline suite and any local run
    # use. Set it and the mentor searches an index instead; the fallback is not a degraded
    # mode but the measured default.
    search_endpoint: str = ""
    search_index: str = "course-passages"
    # Vector search needs an embedding deployment, which is a second model on the Foundry
    # account. Empty means the index is keyword-only and never asks for a vector.
    embedding_deployment: str = ""
    # Vectors are 90% of the index: one course of 200 passages measured 3.92 MB at 1536
    # dimensions, and only twelve of those fit the search tier's 50 MB. text-embedding-3-small
    # is trained so a shortened vector still works, and 512 leaves room for thirty-two.
    embedding_dimensions: int = 512

    # Empty generates one per process, so sessions do not survive a restart. Deliberate: a
    # default in source would be the same signing key on every deployment.
    jwt_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
