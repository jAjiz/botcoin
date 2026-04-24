import os
import pytest

import core.database as database
from exchange import kraken


# ============================================================================
# Kraken API Integration Tests
# ============================================================================


def _kraken_integration_enabled() -> bool:
    return os.getenv("RUN_KRAKEN_INTEGRATION", "false").lower() == "true"


@pytest.fixture(scope="session")
def kraken_enabled() -> bool:
    has_credentials = bool(os.getenv("KRAKEN_API_KEY")) and bool(os.getenv("KRAKEN_API_SECRET"))
    return _kraken_integration_enabled() and has_credentials


@pytest.mark.integration
def test_get_balance(kraken_enabled: bool) -> None:
    if not kraken_enabled:
        pytest.skip("Kraken integration disabled. Set RUN_KRAKEN_INTEGRATION=true with Kraken credentials.")

    balance = kraken.get_balance()

    assert balance is not None
    assert isinstance(balance, dict)


# ============================================================================
# Database Integration Tests
# ============================================================================


def _db_integration_enabled() -> bool:
    return os.getenv("RUN_DB_INTEGRATION", "false").lower() == "true"


@pytest.mark.integration
def test_get_bot_paused() -> None:
    if not _db_integration_enabled():
        pytest.skip("PostgreSQL DAL integration disabled. Set RUN_DB_INTEGRATION=true to run this test.")

    assert isinstance(database.get_bot_paused(), bool)
