"""Aiohttp entrypoint hosting the Bot Framework message endpoint.

Run with `python -m teams_bot.app`. Needs MICROSOFT_APP_ID and MICROSOFT_APP_PASSWORD from an
Azure Bot registration; without them the adapter still starts and only the Bot Framework
Emulator can reach it.
"""

from __future__ import annotations

import logging
import os
import sys

from aiohttp import web
from botbuilder.core import BotFrameworkAdapterSettings
from botbuilder.core.integration import aiohttp_error_middleware
from botbuilder.integration.aiohttp import BotFrameworkHttpAdapter
from botbuilder.schema import Activity
from dotenv import load_dotenv

from teams_bot.backend_client import BackendClient
from teams_bot.bot import LearnForgeBot
from teams_bot.watcher import JobWatcher

# The bot runs as its own process, so nothing else has read .env for it. Without this, setting
# MICROSOFT_APP_ID there would silently do nothing and every Teams call would fail as 401.
load_dotenv(override=False)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

PORT = int(os.getenv("PORT", "3978"))
APP_ID = os.getenv("MICROSOFT_APP_ID", "")

settings = BotFrameworkAdapterSettings(
    app_id=APP_ID,
    app_password=os.getenv("MICROSOFT_APP_PASSWORD", ""),
)
adapter = BotFrameworkHttpAdapter(settings)
client = BackendClient()
bot = LearnForgeBot(client, JobWatcher(adapter, client, APP_ID))


async def on_error(context, error: Exception) -> None:
    logger.exception("teams-bot: unhandled error", exc_info=error)
    await context.send_activity("Sorry, something went wrong.")


adapter.on_turn_error = on_error


async def messages(request: web.Request) -> web.Response:
    if "application/json" not in request.headers.get("Content-Type", ""):
        return web.Response(status=415)
    activity = Activity().deserialize(await request.json())
    auth_header = request.headers.get("Authorization", "")
    response = await adapter.process_activity(activity, auth_header, bot.on_turn)
    if response:
        return web.json_response(data=response.body, status=response.status)
    return web.Response(status=201)


async def health(_: web.Request) -> web.Response:
    """Also reports whether an app id was found, because the failure it prevents — every Teams
    call returning 401 — gives no hint that the id was simply missing."""
    return web.json_response(
        {
            "status": "ok",
            "authenticated": bool(APP_ID),
            "backend": os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8000"),
        }
    )


def build_app() -> web.Application:
    app = web.Application(middlewares=[aiohttp_error_middleware])
    app.router.add_post("/api/messages", messages)
    app.router.add_get("/health", health)
    return app


if __name__ == "__main__":
    try:
        web.run_app(build_app(), host="0.0.0.0", port=PORT)
    except Exception as error:  # pragma: no cover - startup failure path
        logger.exception("teams-bot failed to start")
        sys.exit(1)
