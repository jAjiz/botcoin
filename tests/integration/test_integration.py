import pytest

from exchange import kraken


@pytest.mark.integration
def test_get_balance_live(kraken_live_enabled: bool) -> None:
    if not kraken_live_enabled:
        pytest.skip("Live integration disabled. Set RUN_LIVE_INTEGRATION=true with Kraken credentials.")

    balance = kraken.get_balance()

    assert balance is not None
    assert isinstance(balance, dict)


@pytest.mark.integration
def test_fetch_ohlc_data_live(kraken_live_enabled: bool) -> None:
    if not kraken_live_enabled:
        pytest.skip("Live integration disabled. Set RUN_LIVE_INTEGRATION=true with Kraken credentials.")

    df = kraken.fetch_ohlc_data("XBTEUR", 15)

    assert df is not None
    assert not df.empty
    assert {"open", "high", "low", "close", "volume"}.issubset(df.columns)
