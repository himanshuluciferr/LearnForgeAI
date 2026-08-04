"""Blob Storage access — stores published course artifacts.

Keyless throughout: DefaultAzureCredential only, matching Cosmos, so no account key ever
reaches .env. That choice has a consequence — with no key there is no service SAS, so
read links are user-delegation SAS tokens signed with a key we ask Azure for.

The container is private. A course is written for one employee and often names their team's
systems, so public blob access would hand the whole thing to anyone who guessed the URL.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache

from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob import BlobSasPermissions, ContentSettings, generate_blob_sas
from azure.storage.blob.aio import BlobServiceClient

from backend.config.settings import get_settings

COURSES_CONTAINER = "courses"

# Azure caps a user-delegation key at seven days, and reports anything longer as
# "InvalidXmlNodeValue" rather than as an expiry problem. Six leaves room for the skew
# below and still covers reading a course over a weekend.
LINK_LIFETIME = timedelta(days=6)

# Our clock can sit a little ahead of Azure's, and a key that starts in the future is
# rejected outright. Backdating costs nothing.
CLOCK_SKEW = timedelta(minutes=5)


def blob_enabled() -> bool:
    return bool(get_settings().blob_account_url)


def blob_path(user_id: str, job_id: str, filename: str) -> str:
    """One folder per job under one folder per user, so a user's courses list is a prefix
    scan and two jobs can never overwrite each other."""
    return f"{user_id}/{job_id}/{filename}"


@lru_cache
def _connection() -> tuple[BlobServiceClient, DefaultAzureCredential]:
    # Kept together because closing the client does not close the credential.
    credential = DefaultAzureCredential()
    return BlobServiceClient(get_settings().blob_account_url, credential), credential


async def close_blob_storage() -> None:
    if not _connection.cache_info().currsize:
        return
    client, credential = _connection()
    await client.close()
    await credential.close()
    _connection.cache_clear()


async def read_link(path: str) -> str:
    """A URL that opens the blob and nothing else, and stops working after LINK_LIFETIME."""
    client, _ = _connection()
    start = datetime.now(timezone.utc) - CLOCK_SKEW
    expiry = start + LINK_LIFETIME

    # Signed by Entra rather than by an account key, so revoking the identity revokes the
    # link. The key and the token share one window: a SAS cannot outlive the key anyway.
    key = await client.get_user_delegation_key(start, expiry)
    token = generate_blob_sas(
        account_name=client.account_name,
        container_name=COURSES_CONTAINER,
        blob_name=path,
        user_delegation_key=key,
        permission=BlobSasPermissions(read=True),
        expiry=expiry,
    )
    return f"{client.url.rstrip('/')}/{COURSES_CONTAINER}/{path}?{token}"


async def upload(path: str, content: str, content_type: str) -> str:
    """Overwrites, because republishing the same job must not leave the old course behind."""
    client, _ = _connection()
    blob = client.get_blob_client(COURSES_CONTAINER, path)
    await blob.upload_blob(
        content.encode("utf-8"),
        overwrite=True,
        content_settings=ContentSettings(content_type=content_type),
    )
    return await read_link(path)
