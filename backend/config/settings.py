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

    # Empty endpoint means local development, which keeps the in-memory and file stores.
    cosmos_endpoint: str = ""
    cosmos_database: str = "learnforge"

    # Empty means the publisher renders the course but writes it to generated_courses/
    # instead of uploading it, so local runs need no storage account.
    blob_account_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
