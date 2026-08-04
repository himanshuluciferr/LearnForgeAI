"""Shared pytest fixtures, including mocked Azure services.

This module is imported before any test module, which is the only window in which the
store singletons can still be steered — they are chosen at import time.
"""

import os

# .env now carries a real COSMOS_ENDPOINT, which would silently point the offline suite at
# live Cosmos. A real environment variable outranks .env, so this pins tests to local stores.
os.environ["COSMOS_ENDPOINT"] = ""
