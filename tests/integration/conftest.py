import os

import pytest


def _live_integration_enabled() -> bool:
    return os.getenv("RUN_LIVE_INTEGRATION", "false").lower() == "true"


@pytest.fixture(scope="session")
def kraken_live_enabled() -> bool:
    has_credentials = bool(os.getenv("KRAKEN_API_KEY")) and bool(os.getenv("KRAKEN_API_SECRET"))
    return _live_integration_enabled() and has_credentials
