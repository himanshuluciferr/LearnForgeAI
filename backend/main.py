"""FastAPI application entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api import course, mentor, progress, quiz
from backend.config.settings import get_settings
from backend.services.cosmos import close_cosmos

# Without this our own logger.info calls are silent: uvicorn only configures its own loggers.
logging.basicConfig(
    level=get_settings().log_level,
    format="%(levelname)-8s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # The Cosmos client and its credential hold sockets that must not leak on reload.
    await close_cosmos()


app = FastAPI(title="LearnForge AI", version="0.1.0", lifespan=lifespan)

app.include_router(course.router)
app.include_router(mentor.router)
app.include_router(quiz.router)
app.include_router(progress.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
