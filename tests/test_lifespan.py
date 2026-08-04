"""Shutdown wiring: every service that caches a connection must be closed by lifespan.

This exists because `close_blob_storage` was written and then never called. A test naming
Cosmos and Blob by hand would have passed on the day it was written and missed the next
service just as completely, so this walks `backend.services` instead and fails the moment
a `close_*` function exists that lifespan does not invoke.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

import backend.main
import backend.services
from backend.main import lifespan


def close_functions() -> dict[str, str]:
    """Maps every `close_*` coroutine in backend.services to the module that defines it."""
    found: dict[str, str] = {}

    for module_info in pkgutil.iter_modules(backend.services.__path__):
        module = importlib.import_module(f"backend.services.{module_info.name}")
        for name, value in vars(module).items():
            # Defined here, not imported from a sibling, or one function counts twice.
            if (
                name.startswith("close_")
                and inspect.iscoroutinefunction(value)
                and value.__module__ == module.__name__
            ):
                found[name] = module.__name__

    return found


def test_there_is_something_to_close():
    """A refactor that renames every close function should fail loudly, not silently pass."""
    assert close_functions(), "no close_* coroutines found — this test has stopped testing"


@pytest.mark.asyncio
async def test_shutdown_closes_every_cached_connection(monkeypatch):
    # Captured before patching: the patched stubs are defined in this file, so a second
    # scan would attribute them here and find nothing to expect.
    expected = close_functions()
    called: set[str] = set()

    for name, module_name in expected.items():
        module = importlib.import_module(module_name)

        async def record(_name=name) -> None:
            called.add(_name)

        # Patched where lifespan looks it up, not only where it is defined.
        monkeypatch.setattr(module, name, record)
        if hasattr(backend.main, name):
            monkeypatch.setattr(backend.main, name, record)

    async with lifespan(None):
        pass

    assert called == set(expected)
