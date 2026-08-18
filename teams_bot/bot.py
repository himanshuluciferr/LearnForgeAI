"""LearnForge Teams bot: routes activities to handlers and renders Adaptive Cards.

Deliberately thin. Every decision lives in `handlers`, which take plain dicts and return a
Reply, so the behaviour is testable without an adapter or a running backend.
"""

from __future__ import annotations

import logging

from botbuilder.core import ActivityHandler, CardFactory, MessageFactory, TurnContext

from teams_bot.backend_client import BackendClient
from teams_bot.handlers import card_action_handler, message_handler
from teams_bot.handlers.message_handler import HELP, Reply
from teams_bot.identity import learner_id

logger = logging.getLogger(__name__)


class LearnForgeBot(ActivityHandler):
    def __init__(self, client: BackendClient | None = None) -> None:
        self._client = client or BackendClient()

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
        await self._send(turn_context, reply)

    async def on_members_added_activity(self, members_added, turn_context: TurnContext) -> None:
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await turn_context.send_activity(MessageFactory.text(HELP))

    @staticmethod
    async def _send(turn_context: TurnContext, reply: Reply) -> None:
        if reply.card:
            attachment = CardFactory.adaptive_card(reply.card)
            await turn_context.send_activity(MessageFactory.attachment(attachment))
        else:
            await turn_context.send_activity(MessageFactory.text(reply.text))
