"""FastAPI application entrypoint."""

from fastapi import FastAPI

from backend.api import course, mentor, progress, quiz

app = FastAPI(title="LearnForge AI", version="0.1.0")

app.include_router(course.router)
app.include_router(mentor.router)
app.include_router(quiz.router)
app.include_router(progress.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
