from datetime import UTC, datetime

import core.config as config
from core.utils import (
    PRICE_DECIMALS_FALLBACK,
    VOLUME_DECIMALS_FALLBACK,
    now_utc,
    round_price,
    round_volume,
)


def test_now_utc_returns_timezone_aware_utc_datetime() -> None:
    value = now_utc()
    assert isinstance(value, datetime)
    assert value.tzinfo == UTC
    assert value.year >= 2000


def test_round_price_rounds_to_pair_decimals(monkeypatch) -> None:
    monkeypatch.setitem(config.PAIRS, "USDCEUR", {"pair_decimals": 4})

    assert round_price("USDCEUR", 0.99876543) == 0.9988


def test_round_price_passes_none_through(monkeypatch) -> None:
    monkeypatch.setitem(config.PAIRS, "USDCEUR", {"pair_decimals": 4})

    assert round_price("USDCEUR", None) is None


def test_round_price_falls_back_when_decimals_unknown(monkeypatch) -> None:
    monkeypatch.delitem(config.PAIRS, "ZZZEUR", raising=False)

    assert round_price("ZZZEUR", 1.23456) == round(1.23456, PRICE_DECIMALS_FALLBACK)


def test_round_price_falls_back_when_metadata_is_none(monkeypatch) -> None:
    monkeypatch.setitem(config.PAIRS, "USDCEUR", {"pair_decimals": None})

    assert round_price("USDCEUR", 1.23456) == round(1.23456, PRICE_DECIMALS_FALLBACK)


def test_round_volume_rounds_to_lot_decimals(monkeypatch) -> None:
    monkeypatch.setitem(config.PAIRS, "SOLEUR", {"lot_decimals": 2})

    assert round_volume("SOLEUR", 12.34567) == 12.35


def test_round_volume_passes_none_through(monkeypatch) -> None:
    monkeypatch.setitem(config.PAIRS, "SOLEUR", {"lot_decimals": 2})

    assert round_volume("SOLEUR", None) is None


def test_round_volume_falls_back_when_decimals_unknown(monkeypatch) -> None:
    monkeypatch.delitem(config.PAIRS, "ZZZEUR", raising=False)

    assert round_volume("ZZZEUR", 1.123456789) == round(1.123456789, VOLUME_DECIMALS_FALLBACK)
