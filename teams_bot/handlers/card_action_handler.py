"""Turns an Adaptive Card button press into a reply.

The payload is data we put on the button ourselves, so nothing here parses a label. Same
reason the backend marks a quiz by index: re-deriving what the user pressed is a second
chance to get it wrong.
"""

from __future__ import annotations

from typing import Any

from teams_bot.adaptive_cards import progress_card, quiz_card
from teams_bot.backend_client import BackendClient
from teams_bot.handlers.message_handler import Reply, ready


async def handle(payload: dict[str, Any], user_id: str, client: BackendClient) -> Reply:
    command = (payload or {}).get("command")
    if command == "progress":
        return await _progress(payload, user_id, client)
    if command == "read":
        summary = await client.mark_chapter_read(
            payload["course_id"], user_id, int(payload["chapter"])
        )
        return Reply(card=progress_card.course_progress(summary))
    if command == "quiz":
        paper = await client.quiz(payload["course_id"], user_id, payload.get("chapter"))
        return Reply(card=quiz_card.question(paper, 0))
    if command == "answer":
        return await _answer(payload, user_id, client)
    if command == "confirm":
        await client.confirm(payload["job_id"], user_id)
        return Reply(text="Building it now. Say **progress** whenever you want an update.")
    if command == "choose":
        await client.confirm(payload["job_id"], user_id, payload["choice"])
        return Reply(text=f"Building your **{payload['choice']}** course now.")
    if command == "cancel":
        return Reply(text="Stopped. Tell me what you would like to learn instead.")
    return Reply(text="I did not recognise that button.")


async def _progress(payload: dict[str, Any], user_id: str, client: BackendClient) -> Reply:
    job_id = payload.get("job_id")
    if not job_id:
        summary = await client.course_progress(payload["course_id"], user_id)
        return Reply(card=progress_card.course_progress(summary))

    job = await client.job_progress(job_id, user_id)
    status = job.get("status")
    if status == "needs-confirmation":
        return Reply(card=progress_card.subject_confirmation(job))
    if status == "needs-choice":
        return Reply(card=progress_card.choice(job))
    if status == "completed" and job.get("course_id"):
        return await ready(job["course_id"], user_id, client)
    if status in ("failed", "rejected"):
        return Reply(text=job.get("detail") or job.get("error") or "That run did not finish.")
    return Reply(card=progress_card.generating(job))


async def _answer(payload: dict[str, Any], user_id: str, client: BackendClient) -> Reply:
    """The paper is submitted once, on the last question: the backend marks a whole quiz, and
    a per-question call would score the learner on one answer at a time."""
    course_id, chapter = payload["course_id"], payload.get("chapter")
    answers = {int(number): int(choice) for number, choice in (payload.get("answers") or {}).items()}
    paper = await client.quiz(course_id, user_id, chapter)
    asked = int(payload["index"])
    if asked + 1 < len(paper["questions"]):
        return Reply(card=quiz_card.question(paper, asked + 1, payload.get("answers")))
    marked = await client.submit_answers(course_id, user_id, answers, chapter)
    return Reply(card=quiz_card.result(marked))
