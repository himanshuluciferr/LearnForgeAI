"""Adaptive Card builders. Pure dict in, pure dict out, so a card can be tested by reading it."""

from __future__ import annotations

from typing import Any

SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"
VERSION = "1.5"


def card(*body: dict[str, Any] | None, actions: list[dict[str, Any]] | None = None) -> dict:
    built: dict[str, Any] = {
        "type": "AdaptiveCard",
        "$schema": SCHEMA,
        "version": VERSION,
        "body": [block for block in body if block],
    }
    if actions:
        built["actions"] = actions
    return built


def text(value: str, *, size: str = "default", weight: str = "default") -> dict:
    return {"type": "TextBlock", "text": value, "size": size, "weight": weight, "wrap": True}


def facts(pairs: list[tuple[str, str]]) -> dict[str, Any] | None:
    if not pairs:
        return None
    return {"type": "FactSet", "facts": [{"title": t, "value": v} for t, v in pairs]}


def action(title: str, data: dict[str, Any]) -> dict[str, Any]:
    """A card action carries structured data back, so no handler has to parse a button label."""
    return {"type": "Action.Submit", "title": title, "data": data}


def link(title: str, url: str | None) -> dict[str, Any] | None:
    """None when there is no url, so a course still being written offers no dead button."""
    return {"type": "Action.OpenUrl", "title": title, "url": url} if url else None
