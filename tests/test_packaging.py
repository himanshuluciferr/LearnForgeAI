"""Tests that the image would contain what the server expects to serve.

None of this can be checked by running the app: the paths only matter at build time, and they
are written in three files that have no reason to be edited together.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend import main

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "docker" / "backend.Dockerfile"
VITE_CONFIG = ROOT / "frontend" / "vite.config.ts"
DOCKERIGNORE = ROOT / ".dockerignore"
COMPOSE = ROOT / "docker-compose.yml"


def vite_out_dir() -> str:
    found = re.search(r"outDir:\s*\"([^\"]+)\"", VITE_CONFIG.read_text(encoding="utf-8"))
    assert found, "vite.config.ts no longer declares an outDir"
    return found.group(1)


def copied_from_frontend() -> str:
    found = re.search(
        r"COPY --from=frontend (\S+) (\S+)", DOCKERFILE.read_text(encoding="utf-8")
    )
    assert found, "the Dockerfile no longer copies the built app out of the frontend stage"
    return found.group(1)


def test_the_dockerfile_copies_from_where_vite_writes():
    """The build stage runs in /frontend, so vite's ../backend/static lands in /backend/static.
    Change one without the other and the image quietly ships no app at all."""
    assert vite_out_dir() == "../backend/static"
    assert copied_from_frontend() == "/backend/static"


def test_the_app_is_copied_to_where_the_server_looks_for_it():
    _, destination = re.search(
        r"COPY --from=frontend (\S+) (\S+)", DOCKERFILE.read_text(encoding="utf-8")
    ).groups()
    served = main.STATIC.relative_to(ROOT).as_posix()

    assert destination.lstrip("./") == served


def test_the_frontend_is_built_rather_than_assumed():
    body = DOCKERFILE.read_text(encoding="utf-8")

    assert "npm ci" in body, "an image that runs npm install ignores the lockfile"
    assert "npm run build" in body


@pytest.mark.parametrize("excluded", ["node_modules/", "backend/static/", ".env"])
def test_the_build_context_leaves_out_what_it_must(excluded):
    """node_modules makes the context enormous, a local backend/static would beat the one the
    image just built, and .env in a layer is a leaked secret."""
    lines = {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert excluded in lines


def test_the_source_mount_does_not_hide_the_built_app():
    """compose mounts ./backend over /app/backend for reload. Without an anonymous volume on
    top, that hides the bundle, and backend/static is gitignored so a fresh clone has none."""
    body = COMPOSE.read_text(encoding="utf-8")

    assert "- /app/backend/static" in body
