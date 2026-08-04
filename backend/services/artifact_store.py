"""Published artifact persistence.

Blob Storage when an account is configured, otherwise files under generated_courses/,
which keeps the rendered course readable on disk during development.

Mirrors course_store: same Protocol-and-two-implementations shape, chosen once at import.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

from backend.services.blob_storage import blob_enabled, blob_path, upload

ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "generated_courses"

MARKDOWN = "text/markdown; charset=utf-8"


class ArtifactStore(Protocol):
    async def put(self, user_id: str, job_id: str, filename: str, content: str) -> str:
        """Stores the artifact and returns a URL that will open it."""
        ...


class FileArtifactStore:
    def __init__(self, directory: Path = ARTIFACTS_DIR) -> None:
        self._dir = directory

    async def put(self, user_id: str, job_id: str, filename: str, content: str) -> str:
        # user_id is deliberately not in the path here: locally there is one user, and
        # keeping ids out of the filesystem avoids having to sanitise them for path escapes.
        path = self._dir / job_id / filename

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        await asyncio.to_thread(write)
        return path.as_uri()


class BlobArtifactStore:
    async def put(self, user_id: str, job_id: str, filename: str, content: str) -> str:
        return await upload(blob_path(user_id, job_id, filename), content, MARKDOWN)


artifact_store: ArtifactStore = BlobArtifactStore() if blob_enabled() else FileArtifactStore()
