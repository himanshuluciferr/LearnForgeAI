"""Keeps a progress card up to date without the learner pressing anything.

Teams has no way for a card to poll, so the update has to be pushed: the conversation reference
captured when the run started lets the bot start a new turn later, and update_activity replaces
the card that is already on screen rather than adding another one below it.

The alternative — an Adaptive Card `refresh` block — only refreshes when the card is looked at,
and not at all in the Emulator, so a learner watching the screen would see nothing move.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from botbuilder.core import CardFactory, MessageFactory, TurnContext
from botbuilder.schema import Activity, ActivityTypes, ConversationReference

from teams_bot.adaptive_cards import progress_card
from teams_bot.backend_client import BackendClient
from teams_bot.handlers.message_handler import UNFINISHED, job_reply

logger = logging.getLogger(__name__)

POLL_SECONDS = 10
# Generation runs about twenty minutes; this is the ceiling before we stop watching and leave
# the learner with a card they can still press.
MAX_POLLS = 300


class JobWatcher:
    """Polls one run and edits its card in place until the run stops moving."""

    def __init__(self, adapter: Any, client: BackendClient, poll_seconds: int = POLL_SECONDS):
        self._adapter = adapter
        self._client = client
        self._poll_seconds = poll_seconds
        self._watching: set[str] = set()

    def watch(
        self, reference: ConversationReference, activity_id: str, job_id: str, user_id: str
    ) -> asyncio.Task | None:
        """One watcher per job: pressing Check again while one runs must not start a second."""
        if not activity_id or job_id in self._watching:
            return None
        self._watching.add(job_id)
        return asyncio.create_task(self._run(reference, activity_id, job_id, user_id))

    async def _run(
        self, reference: ConversationReference, activity_id: str, job_id: str, user_id: str
    ) -> None:
        try:
            last: tuple[Any, Any] | None = None
            for _ in range(MAX_POLLS):
                await asyncio.sleep(self._poll_seconds)
                job = await self._client.job_progress(job_id, user_id)
                moved = (job.get("status"), job.get("percent"))
                # Only redraw when something changed: an edit that changes nothing still makes
                # the client re-render, and Teams rate-limits updates.
                if moved != last:
                    last = moved
                    await self._show(reference, activity_id, job, user_id)
                if job.get("status") not in UNFINISHED:
                    return
        except Exception:
            logger.exception("teams-bot: watching job %s failed", job_id)
        finally:
            self._watching.discard(job_id)

    async def _show(
        self, reference: ConversationReference, activity_id: str, job: dict, user_id: str
    ) -> None:
        reply = await job_reply(job, user_id, self._client)

        async def edit(turn_context: TurnContext) -> None:
            activity = (
                MessageFactory.attachment(CardFactory.adaptive_card(reply.card))
                if reply.card
                else MessageFactory.text(reply.text)
            )
            activity.id = activity_id
            activity.type = ActivityTypes.message
            await turn_context.update_activity(activity)

        await self._adapter.continue_conversation(reference, edit, reference.bot.id)


def still_running(job: dict) -> bool:
    return job.get("status") in UNFINISHED


__all__ = ["JobWatcher", "still_running", "Activity"]
