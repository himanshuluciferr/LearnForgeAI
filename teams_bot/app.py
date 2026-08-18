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

from teams_bot.bot import LearnForgeBot

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

PORT = int(os.getenv("PORT", "3978"))

settings = BotFrameworkAdapterSettings(
    app_id=os.getenv("MICROSOFT_APP_ID", ""),
    app_password=os.getenv("MICROSOFT_APP_PASSWORD", ""),
)
adapter = BotFrameworkHttpAdapter(settings)
bot = LearnForgeBot()


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
    return web.json_response({"status": "ok"})


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
