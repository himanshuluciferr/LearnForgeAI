"""Turns a learner's message into a reply.

Returns text or a card rather than sending anything, so every route can be tested without a
Teams adapter or a running backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from teams_bot.adaptive_cards import course_card, progress_card, quiz_card
from teams_bot.backend_client import BackendClient
from teams_bot.commands import Command, Intent, read

HELP = (
    "I build a course on anything you want to learn, then quiz you on it.\n\n"
    "- **teach me kubernetes operators** — start a course\n"
    "- **progress** — see how far you have got\n"
    "- **quiz me** — take the next quiz\n\n"
    "A course takes about twenty minutes to build. Ask for progress whenever you like."
)

WORKING = (
    "Building your course now. It takes about twenty minutes because every chapter is written "
    "from documentation we actually fetch. Say **progress** whenever you want an update."
)


@dataclass(frozen=True)
class Reply:
    """Text or a card, never both: two things arriving at once reads as two answers."""

    text: str = ""
    card: dict[str, Any] | None = None


async def latest_course(client: BackendClient, user_id: str) -> dict[str, Any] | None:
    courses = await client.list_courses(user_id)
    return courses[0] if courses else None


async def handle(text: str | None, user_id: str, client: BackendClient) -> Reply:
    command = read(text)
    if command.intent is Intent.HELP:
        return Reply(text=HELP)
    if command.intent is Intent.TEACH:
        return await start(command, user_id, client)
    if command.intent is Intent.PROGRESS:
        return await progress(user_id, client)
    if command.intent is Intent.QUIZ:
        return await quiz(command, user_id, client)
    return Reply(text="I cannot answer questions about a course yet, but I can quiz you on one.")


async def start(command: Command, user_id: str, client: BackendClient) -> Reply:
    job = await client.start_course(user_id, command.text)
    return Reply(text=f"{WORKING}\n\n_Job {job['job_id']}_")


async def progress(user_id: str, client: BackendClient) -> Reply:
    """A learner asking for progress may be waiting on a build or working through a finished
    course, and the answer is different. The job is checked first because it is the one that
    changes minute to minute."""
    course = await latest_course(client, user_id)
    if course is None:
        return Reply(text="You have no courses yet. Try **teach me kubernetes operators**.")
    summary = await client.course_progress(course["course_id"], user_id)
    return Reply(card=progress_card.course_progress(summary))


async def quiz(command: Command, user_id: str, client: BackendClient) -> Reply:
    course = await latest_course(client, user_id)
    if course is None:
        return Reply(text="There is nothing to quiz you on yet.")
    chapter = command.chapter
    if chapter is None:
        summary = await client.course_progress(course["course_id"], user_id)
        # The quiz that helps is the one on the chapter just read, not the one coming next.
        chapter = next(
            (c["number"] for c in reversed(summary["chapters"]) if c["read"]), None
        )
    paper = await client.quiz(course["course_id"], user_id, chapter)
    if not paper.get("questions"):
        return Reply(text="That quiz has no questions.")
    return Reply(card=quiz_card.question(paper, 0))


async def ready(course_id: str, user_id: str, client: BackendClient) -> Reply:
    summary = await client.course_progress(course_id, user_id)
    return Reply(card=course_card.ready(course_id, summary))
