"""The card shown when a course has finished generating."""

from __future__ import annotations

from typing import Any

from teams_bot.adaptive_cards import action, card, facts, text


def ready(course_id: str, progress: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = progress or {}
    total = summary.get("chapters_total", 0)
    return card(
        text(summary.get("title") or "Your course is ready", size="large", weight="bolder"),
        facts([("Chapters", str(total))] if total else []),
        actions=[
            action("Show my progress", {"command": "progress", "course_id": course_id}),
            action("Quiz me", {"command": "quiz", "course_id": course_id}),
        ],
    )
