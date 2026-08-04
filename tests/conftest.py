"""Shared pytest fixtures, including mocked Azure services.

This module is imported before any test module, which is the only window in which the
store singletons can still be steered — they are chosen at import time.
"""

import os

# .env now carries real endpoints, which would silently point the offline suite at live
# Azure. A real environment variable outranks .env, so this pins tests to local stores.
# Any new endpoint setting must be added here the day it lands in .env.
os.environ["COSMOS_ENDPOINT"] = ""
os.environ["BLOB_ACCOUNT_URL"] = ""

import pytest  # noqa: E402

from backend.agents import fanout  # noqa: E402


@pytest.fixture(autouse=True)
def instant_backoff(request, monkeypatch):
    """Retry backoff is real seconds. Offline tests assert sequence, not the clock.

    Live tests keep the real delay — a retry there is a real 429 worth waiting out.
    """
    if "live" not in request.keywords:
        monkeypatch.setattr(fanout, "backoff", lambda attempt: 0.0)
