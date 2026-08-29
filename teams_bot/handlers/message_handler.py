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
    "- **quiz me** — take the next quiz\n"
    "- ask me anything about a course you are taking — I answer from what it actually says\n\n"
    "A course takes about twenty minutes to build. Ask for progress whenever you like."
)

WORKING = (
    "Building your course now. It takes about twenty minutes because every chapter is written "
    "from documentation we actually fetch. I will ask you to confirm the subject first."
)

# A run in one of these has not produced a course yet, so progress means the run, not a course.
UNFINISHED = ("queued", "running", "needs-confirmation", "needs-choice")


@dataclass(frozen=True)
class Reply:
    """Text or a card, never both: two things arriving at once reads as two answers."""

    text: str = ""
    card: dict[str, Any] | None = None
    # Set when this card is worth keeping up to date, which the bot does by editing it in place.
    watch_job: str | None = None


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
    return await ask(command, user_id, client)


async def ask(command: Command, user_id: str, client: BackendClient) -> Reply:
    """A question is about the course the learner has, so there is nothing to answer from
    until they have one."""
    course = await latest_course(client, user_id)
    if course is None:
        return Reply(
            text="I answer questions about a course you are taking, and you have none yet. "
            "Try **teach me kubernetes operators**."
        )
    reply = await client.ask(course["course_id"], user_id, command.text)
    chapter = reply.get("chapter_number")
    if chapter:
        note = f"\n\n_Chapter {chapter} covers this._"
    elif reply.get("looked_up"):
        # Said plainly, or they go hunting for it in a chapter that never had it.
        note = "\n\n_Not in your course — I read this up just now._"
    else:
        note = ""
    return Reply(text=f"{reply['answer']}{note}")


async def start(command: Command, user_id: str, client: BackendClient) -> Reply:
    job = await client.start_course(user_id, command.text)
    # A card rather than the job id in prose: the learner should not have to retype anything,
    # and the run stops to ask which subject it found.
    return Reply(card=progress_card.started(job, WORKING))


async def progress(user_id: str, client: BackendClient) -> Reply:
    """A run in flight is the answer if there is one, because it is what changes minute to
    minute; otherwise the newest finished course."""
    for job in await client.list_jobs(user_id):
        if job.get("status") in UNFINISHED:
            return await job_reply(job, user_id, client)

    course = await latest_course(client, user_id)
    if course is None:
        return Reply(text="You have no courses yet. Try **teach me kubernetes operators**.")
    summary = await client.course_progress(course["course_id"], user_id)
    return Reply(card=progress_card.course_progress(summary))


async def job_reply(job: dict[str, Any], user_id: str, client: BackendClient) -> Reply:
    """One place that turns a job into a reply, so a button press and a typed word cannot
    answer the same state differently."""
    status = job.get("status")
    if status == "needs-confirmation":
        return Reply(card=progress_card.subject_confirmation(job))
    if status == "needs-choice":
        return Reply(card=progress_card.choice(job))
    if status == "completed" and job.get("course_id"):
        return await ready(job["course_id"], user_id, client)
    if status in ("failed", "rejected"):
        return Reply(text=job.get("detail") or job.get("error") or "That run did not finish.")
    return Reply(card=progress_card.generating(job), watch_job=job.get("job_id"))


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
