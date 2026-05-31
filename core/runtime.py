import threading
from datetime import datetime
from typing import Any

_lock = threading.Lock()
_shared_data: dict[str, Any] = {
    "last_balance": {},
    "pairs_data": {},  # {pair: {"last_price": float, "atr": float, "volatility_level": str}}
    "last_run_at": None,
    "calibration_cache": {},  # {pair: {"lookback_days": int, "up_events": list, "down_events": list}}
}


def update_balance(balance: dict[str, Any] | None) -> None:
    with _lock:
        _shared_data["last_balance"] = balance if balance else {}


def get_last_balance() -> dict[str, Any]:
    with _lock:
        return dict(_shared_data["last_balance"])


def update_pair_data(
    pair: str,
    price: float | None = None,
    atr: float | None = None,
    volatility_level: str | None = None,
) -> None:
    with _lock:
        if pair not in _shared_data["pairs_data"]:
            _shared_data["pairs_data"][pair] = {}
        if price is not None:
            _shared_data["pairs_data"][pair]["last_price"] = price
        if atr is not None:
            _shared_data["pairs_data"][pair]["atr"] = atr
        if volatility_level is not None:
            _shared_data["pairs_data"][pair]["volatility_level"] = volatility_level


def get_pair_data(pair: str) -> dict[str, Any]:
    with _lock:
        return dict(_shared_data["pairs_data"].get(pair, {}))


def update_last_run_at(last_run_at: datetime) -> None:
    with _lock:
        _shared_data["last_run_at"] = last_run_at


def get_last_run_at() -> datetime | None:
    with _lock:
        return _shared_data["last_run_at"]


def update_calibration_cache(pair: str, lookback_days: int, up_events: list, down_events: list) -> None:
    """Cache the structural-noise events and chosen lookback window for a pair."""
    with _lock:
        _shared_data["calibration_cache"][pair] = {
            "lookback_days": lookback_days,
            "up_events": up_events,
            "down_events": down_events,
        }


def get_calibration_cache(pair: str) -> dict[str, Any] | None:
    """Return the cached calibration data for a pair, or None if not yet computed."""
    with _lock:
        return _shared_data["calibration_cache"].get(pair)
