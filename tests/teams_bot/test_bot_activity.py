"""Tests for the activity handler.

bot.py is the only part that touches the SDK, so it is exercised through a stub TurnContext
rather than an adapter — no registration, no emulator, no network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from teams_bot.backend_client import BackendClient
from teams_bot.bot import LearnForgeBot


class StubContext:
    """Records what the bot sent. send_activity and update_activity are the whole surface
    bot.py uses."""

    def __init__(self, activity) -> None:
        self.activity = activity
        self.sent: list[object] = []
        self.edited: list[object] = []

    async def send_activity(self, activity) -> object:
        self.sent.append(activity)
        return SimpleNamespace(id="new-id")

    async def update_activity(self, activity) -> None:
        self.edited.append(activity)


def incoming(text: str | None = None, value: dict | None = None, reply_to_id: str | None = None):
    return SimpleNamespace(
        text=text,
        value=value,
        reply_to_id=reply_to_id,
        from_property=SimpleNamespace(aad_object_id="aad-1", id="channel-1"),
        recipient=SimpleNamespace(id="bot"),
    )


def bot_for(handler) -> LearnForgeBot:
    transport = httpx.MockTransport(handler)
    client = BackendClient(base_url="http://backend", client=httpx.AsyncClient(transport=transport))
    return LearnForgeBot(client)


def sent_text(context: StubContext) -> str:
    return " ".join(str(getattr(activity, "text", "") or "") for activity in context.sent)


def sent_cards(context: StubContext) -> str:
    return json.dumps(
        [
            attachment.content
            for activity in context.sent + context.edited
            for attachment in (getattr(activity, "attachments", None) or [])
        ]
    )


@pytest.mark.asyncio
async def test_a_plain_message_is_answered():
    context = StubContext(incoming(text="help"))

    await bot_for(lambda request: httpx.Response(200, json={})).on_message_activity(context)

    assert "teach me" in sent_text(context)


@pytest.mark.asyncio
async def test_a_card_press_replaces_the_card_it_came_from():
    """Answering underneath leaves the pressed card on screen still showing what was true
    before it was pressed, so the chat fills with stale progress bars."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"job_id": "j1", "status": "running", "percent": 20, "step": "research"}
        )

    context = StubContext(
        incoming(value={"command": "progress", "job_id": "j1"}, reply_to_id="card-3")
    )

    await bot_for(handler).on_message_activity(context)

    assert context.sent == []
    assert [activity.id for activity in context.edited] == ["card-3"]
    assert "20%" in sent_cards(context)


@pytest.mark.asyncio
async def test_a_typed_message_is_answered_with_a_new_message():
    """Only a button press has a card to replace."""
    context = StubContext(incoming(text="help"))

    await bot_for(lambda request: httpx.Response(200, json={})).on_message_activity(context)

    assert context.edited == [] and len(context.sent) == 1


@pytest.mark.asyncio
async def test_a_channel_that_refuses_to_edit_still_gets_an_answer():
    """A reply underneath beats no reply at all."""

    class NoEdits(StubContext):
        async def update_activity(self, activity):
            raise RuntimeError("this channel does not allow edits")

    context = NoEdits(
        incoming(value={"command": "progress", "job_id": "j1"}, reply_to_id="card-3")
    )

    await bot_for(
        lambda request: httpx.Response(
            200, json={"job_id": "j1", "status": "running", "percent": 20, "step": "research"}
        )
    ).on_message_activity(context)

    assert len(context.sent) == 1 and getattr(context.sent[0], "id", None) is None


@pytest.mark.asyncio
async def test_a_backend_failure_reaches_the_learner_as_a_sentence():
    """A stack trace in a chat window helps nobody, and a silent turn reads as the bot being
    broken."""
    context = StubContext(incoming(text="progress"))

    await bot_for(lambda request: httpx.Response(500, json={})).on_message_activity(context)

    assert "something went wrong" in sent_text(context).lower()


@pytest.mark.asyncio
async def test_joining_a_chat_explains_what_the_bot_does():
    context = StubContext(incoming())
    added = [SimpleNamespace(id="someone")]

    await bot_for(lambda request: httpx.Response(200, json={})).on_members_added_activity(
        added, context
    )

    assert "teach me" in sent_text(context)


@pytest.mark.asyncio
async def test_the_bot_does_not_greet_itself():
    context = StubContext(incoming())
    added = [SimpleNamespace(id="bot")]

    await bot_for(lambda request: httpx.Response(200, json={})).on_members_added_activity(
        added, context
    )

    assert context.sent == []
