"""The bot process has to be able to start.

app.py wires the adapter, the client and the watcher together at import time, and nothing else
in the suite imports it — so a signature change in any of them broke the entry point while 613
tests stayed green. The failure only appeared in a clean-worktree import check, after a push.
"""

from __future__ import annotations


def test_the_bot_entry_point_imports():
    import teams_bot.app as app

    assert app.bot is not None and app.adapter is not None


def test_it_builds_an_aiohttp_app_with_the_endpoint_teams_posts_to():
    import teams_bot.app as app

    routes = {
        getattr(route.resource, "canonical", None) for route in app.build_app().router.routes()
    }

    assert "/api/messages" in routes and "/health" in routes
