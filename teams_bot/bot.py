"""LearnForge Teams bot: routes activities to handlers and renders Adaptive Cards.

Deliberately thin. Every decision lives in `handlers`, which take plain dicts and return a
Reply, so the behaviour is testable without an adapter or a running backend.
"""

from __future__ import annotations

import logging
from typing import Any

from botbuilder.core import ActivityHandler, CardFactory, MessageFactory, TurnContext

from teams_bot.backend_client import BackendClient
from teams_bot.handlers import card_action_handler, message_handler
from teams_bot.handlers.message_handler import HELP, Reply
from teams_bot.identity import learner_id

logger = logging.getLogger(__name__)


class LearnForgeBot(ActivityHandler):
    def __init__(self, client: BackendClient | None = None, watcher: Any = None) -> None:
        self._client = client or BackendClient()
        self._watcher = watcher

    async def on_message_activity(self, turn_context: TurnContext) -> None:
        activity = turn_context.activity
        user_id = learner_id(activity)
        try:
            if activity.value:
                reply = await card_action_handler.handle(activity.value, user_id, self._client)
            else:
                reply = await message_handler.handle(activity.text, user_id, self._client)
        except Exception:
            # The learner gets a sentence rather than a stack trace, and we keep the trace.
            logger.exception("teams-bot: turn failed for %s", user_id)
            reply = Reply(text="Something went wrong reaching the course service. Try again.")

        shown = await self._reply(turn_context, reply)
        if reply.watch_job and self._watcher and shown:
            self._watcher.watch(
                TurnContext.get_conversation_reference(activity), shown, reply.watch_job, user_id
            )

    async def on_members_added_activity(self, members_added, turn_context: TurnContext) -> None:
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await turn_context.send_activity(MessageFactory.text(HELP))

    async def _reply(self, turn_context: TurnContext, reply: Reply) -> str | None:
        """Returns the id of the message now showing this reply, which is what a later edit
        needs.

        A button press replaces the card it came from. Answering underneath leaves the pressed
        card on screen still showing what was true before it was pressed, so a conversation
        ends up as a column of stale progress bars.
        """
        activity = self._as_activity(reply)
        pressed = turn_context.activity.reply_to_id if turn_context.activity.value else None
        if pressed:
            activity.id = pressed
            try:
                await turn_context.update_activity(activity)
                return pressed
            except Exception:
                # Some channels refuse to edit; a reply underneath beats no reply at all.
                logger.warning("teams-bot: could not edit %s, sending instead", pressed)
                activity.id = None

        sent = await turn_context.send_activity(activity)
        return getattr(sent, "id", None)

    @staticmethod
    def _as_activity(reply: Reply):
        if reply.card:
            return MessageFactory.attachment(CardFactory.adaptive_card(reply.card))
        return MessageFactory.text(reply.text)
