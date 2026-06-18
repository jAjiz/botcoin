import threading
from datetime import datetime
from typing import Any

from core.utils import now_utc

_lock = threading.Lock()
_shared_data = {
    "last_balance": {},
    "pairs_data": {},  # {pair: {"last_price": float, "atr": float}}
    "last_run_at": None,
    "pair_calibration": {},  # {pair: {
    #   "up_events": list[dict],
    #   "down_events": list[dict],
    #   "atr_p20": float, "atr_p50": float, "atr_p80": float, "atr_p95": float,
    #   "row_count": int,        # rows in the df used to compute these
    #   "computed_at": datetime,
    # }}
    # Phase 11 extends this entry with "window_days" + "window_sweep".
    "config_dirty": set(),  # pairs whose config changed since the last scheduler check
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


def update_pair_calibration(
    pair: str,
    up_events: list[dict[str, Any]],
    down_events: list[dict[str, Any]],
    atr_p20: float,
    atr_p50: float,
    atr_p80: float,
    atr_p95: float,
    row_count: int,
) -> None:
    with _lock:
        _shared_data["pair_calibration"][pair] = {
            "up_events": up_events,
            "down_events": down_events,
            "atr_p20": atr_p20,
            "atr_p50": atr_p50,
            "atr_p80": atr_p80,
            "atr_p95": atr_p95,
            "row_count": row_count,
            "computed_at": now_utc(),
        }


def get_pair_calibration(pair: str) -> dict[str, Any] | None:
    with _lock:
        entry = _shared_data["pair_calibration"].get(pair)
        return None if entry is None else dict(entry)  # shallow copy, matches existing pattern


def mark_config_dirty(pair: str) -> None:
    with _lock:
        _shared_data["config_dirty"].add(pair)


def pop_config_dirty(pair: str) -> bool:
    """Return True (and clear) if pair's config changed since the last check."""
    with _lock:
        if pair in _shared_data["config_dirty"]:
            _shared_data["config_dirty"].discard(pair)
            return True
        return False
