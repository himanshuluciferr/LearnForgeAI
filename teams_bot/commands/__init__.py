"""Parsing a Teams message into an intent.

Pure functions with no bot SDK in sight, so routing can be tested without an adapter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Intent(StrEnum):
    TEACH = "teach"
    PROGRESS = "progress"
    QUIZ = "quiz"
    MENTOR = "mentor"
    HELP = "help"


# Teams prefixes a mention to every channel message; it is markup, not what the learner said.
MENTION = re.compile(r"<at>.*?</at>", re.IGNORECASE)
CHAPTER = re.compile(r"\bchapter\s+(\d+)\b", re.IGNORECASE)

VERBS: tuple[tuple[Intent, tuple[str, ...]], ...] = (
    (Intent.HELP, ("help", "hi", "hello", "what can you do")),
    (Intent.PROGRESS, ("progress", "how am i doing", "where was i")),
    (Intent.QUIZ, ("quiz", "test me")),
    (Intent.MENTOR, ("ask", "explain")),
    (Intent.TEACH, ("teach me", "teach", "learn", "course on")),
)


@dataclass(frozen=True)
class Command:
    intent: Intent
    text: str = ""
    chapter: int | None = None


def clean(text: str | None) -> str:
    return MENTION.sub(" ", text or "").strip()


def read(text: str | None) -> Command:
    """The first verb that matches wins, and an unrecognised message is a request to learn.

    Defaulting to TEACH rather than HELP is deliberate: "Kubernetes operators" is a course
    request, and requirement-agent already refuses anything that is not one — with a better
    message than a keyword list could write.
    """
    said = clean(text)
    if not said:
        return Command(Intent.HELP)

    lowered = said.lower()
    found = CHAPTER.search(lowered)
    number = int(found.group(1)) if found else None
    for intent, verbs in VERBS:
        if any(lowered.startswith(verb) or f" {verb} " in f" {lowered} " for verb in verbs):
            return Command(intent, said, number)
    return Command(Intent.TEACH, said, number)
