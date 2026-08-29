"""FastAPI application entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api import auth, course, job, mentor, progress, quiz
from backend.config.settings import get_settings
from backend.services.blob_storage import close_blob_storage
from backend.services.ai_search import close_search
from backend.services.cosmos import close_cosmos

# Anything the API owns. The app's own routes are namespaced under /read so they cannot
# collide: one url cannot mean both a JSON document and a page.
API_PREFIXES = {"auth", "courses", "jobs", "mentor", "quiz", "progress", "health", "assets"}

# Without this our own logger.info calls are silent: uvicorn only configures its own loggers.
logging.basicConfig(
    level=get_settings().log_level,
    format="%(levelname)-8s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # Both clients hold sockets and a credential that must not leak on reload. Every
    # service with a cached connection belongs here the day it is written.
    await close_cosmos()
    await close_blob_storage()
    await close_search()


app = FastAPI(title="LearnForge AI", version="0.1.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(course.router)
app.include_router(job.router)
app.include_router(mentor.router)
app.include_router(quiz.router)
app.include_router(progress.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# The built React app, if it has been built. One origin for the app and the API, so there is
# one url to deploy and no CORS.
STATIC = Path(__file__).resolve().parent / "static"

if STATIC.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        """Serves index.html so a deep link survives a reload; React does the routing.

        Declared last, so every API route is matched first. A path under an API prefix that
        got this far is a real 404 and must say so rather than answering with a page.
        """
        if path.split("/")[0] in API_PREFIXES:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        return FileResponse(STATIC / "index.html")
