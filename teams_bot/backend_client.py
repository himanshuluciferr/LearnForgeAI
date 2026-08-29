"""Backend client — the only place teams_bot talks to the FastAPI backend.

Every call carries the learner's id, because the backend requires it to both route to the
Cosmos partition and authorise the read.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

BASE_URL = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8000")
# Generation runs in the background, so no call here should ever be slow. A long timeout would
# only turn a backend problem into a Teams turn that never answers.
TIMEOUT = 20


class BackendClient:
    def __init__(self, base_url: str = BASE_URL, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._client is not None:
            response = await self._client.request(method, f"{self._base_url}{path}", **kwargs)
        else:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.request(method, f"{self._base_url}{path}", **kwargs)
        response.raise_for_status()
        return response.json()

    async def start_course(self, user_id: str, prompt: str) -> dict:
        return await self._request(
            "POST", "/courses", json={"user_id": user_id, "prompt": prompt}
        )

    async def list_courses(self, user_id: str, limit: int = 10) -> list[dict]:
        return await self._request(
            "GET", "/courses", params={"user_id": user_id, "limit": limit}
        )

    async def list_jobs(self, user_id: str, limit: int = 5) -> list[dict]:
        return await self._request("GET", "/jobs", params={"user_id": user_id, "limit": limit})

    async def ask(self, course_id: str, user_id: str, question: str) -> dict:
        return await self._request(
            "POST",
            f"/mentor/{course_id}",
            params={"user_id": user_id},
            json={"question": question},
        )

    async def job_progress(self, job_id: str, user_id: str) -> dict:
        return await self._request(
            "GET", f"/courses/{job_id}/progress", params={"user_id": user_id}
        )

    async def confirm(self, job_id: str, user_id: str, choice: str | None = None) -> dict:
        body = {"choice": choice} if choice else None
        return await self._request(
            "POST", f"/courses/{job_id}/confirm", params={"user_id": user_id}, json=body
        )

    async def course_progress(self, course_id: str, user_id: str) -> dict:
        return await self._request("GET", f"/progress/{course_id}", params={"user_id": user_id})

    async def mark_chapter_read(self, course_id: str, user_id: str, number: int) -> dict:
        return await self._request(
            "PUT", f"/progress/{course_id}/chapters/{number}", params={"user_id": user_id}
        )

    async def quiz(self, course_id: str, user_id: str, chapter: int | None = None) -> dict:
        params: dict[str, Any] = {"user_id": user_id}
        if chapter is not None:
            params["chapter"] = chapter
        return await self._request("GET", f"/quiz/{course_id}", params=params)

    async def submit_answers(
        self, course_id: str, user_id: str, answers: dict[int, int], chapter: int | None = None
    ) -> dict:
        params: dict[str, Any] = {"user_id": user_id}
        if chapter is not None:
            params["chapter"] = chapter
        return await self._request(
            "POST",
            f"/quiz/{course_id}/answers",
            params=params,
            json={"answers": {str(k): v for k, v in answers.items()}},
        )
