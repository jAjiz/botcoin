import threading
from datetime import datetime
from typing import Any

from core.utils import now_utc

_lock = threading.Lock()
_shared_data = {
    "last_balance": {},
    "pairs_data": {},  # {pair: {"last_price": float, "atr": float}}
    "last_run_at": None,
    "pair_calibration": {},  # {pair: entry}; see update_pair_calibration for the shape
    "config_dirty": set(),  # pairs whose config changed since the last scheduler check
    "consecutive_session_failures": 0,
    "session_failure_alerted": False,
    "consecutive_session_overruns": 0,
    "session_overrun_alerted": False,
    "consecutive_pair_failures": {},  # {pair: count}
    "pair_failure_alerted": set(),  # pairs already alerted on
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
    atr_ratio_p20: float,
    atr_ratio_p50: float,
    atr_ratio_p80: float,
    atr_ratio_p95: float,
    row_count: int,
) -> None:
    """Snapshot a pair's calibration. The percentiles are of ATR/close, not of ATR."""
    with _lock:
        _shared_data["pair_calibration"][pair] = {
            "up_events": up_events,
            "down_events": down_events,
            "atr_ratio_p20": atr_ratio_p20,
            "atr_ratio_p50": atr_ratio_p50,
            "atr_ratio_p80": atr_ratio_p80,
            "atr_ratio_p95": atr_ratio_p95,
            "row_count": row_count,
            "computed_at": now_utc(),
        }


def get_pair_calibration(pair: str) -> dict[str, Any] | None:
    with _lock:
        entry = _shared_data["pair_calibration"].get(pair)
        return None if entry is None else dict(entry)


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


def register_session_failure(threshold: int) -> int | None:
    """Count a failed session; return the streak count the first tick it reaches ``threshold``."""
    with _lock:
        _shared_data["consecutive_session_failures"] += 1
        count = _shared_data["consecutive_session_failures"]
        if count >= threshold and not _shared_data["session_failure_alerted"]:
            _shared_data["session_failure_alerted"] = True
            return count
        return None


def register_session_success() -> bool:
    """Reset the failure streak; return True if we had alerted, so the caller announces recovery."""
    with _lock:
        was_alerted = _shared_data["session_failure_alerted"]
        _shared_data["consecutive_session_failures"] = 0
        _shared_data["session_failure_alerted"] = False
        return was_alerted


def register_pair_failure(pair: str, threshold: int) -> int | None:
    """Count a session in which ``pair`` failed; return the streak count the first tick it reaches ``threshold``."""
    with _lock:
        count = _shared_data["consecutive_pair_failures"].get(pair, 0) + 1
        _shared_data["consecutive_pair_failures"][pair] = count
        if count >= threshold and pair not in _shared_data["pair_failure_alerted"]:
            _shared_data["pair_failure_alerted"].add(pair)
            return count
        return None


def register_pair_success(pair: str) -> bool:
    """Reset ``pair``'s failure streak; return True if we had alerted on it."""
    with _lock:
        _shared_data["consecutive_pair_failures"][pair] = 0
        was_alerted = pair in _shared_data["pair_failure_alerted"]
        _shared_data["pair_failure_alerted"].discard(pair)
        return was_alerted


def register_session_overrun(threshold: int) -> int | None:
    """Count a session that overran the interval; return the streak count the first tick it reaches ``threshold``."""
    with _lock:
        _shared_data["consecutive_session_overruns"] += 1
        count = _shared_data["consecutive_session_overruns"]
        if count >= threshold and not _shared_data["session_overrun_alerted"]:
            _shared_data["session_overrun_alerted"] = True
            return count
        return None


def register_session_ontime() -> bool:
    """Reset the overrun streak; return True if we had alerted, so the caller announces recovery."""
    with _lock:
        was_alerted = _shared_data["session_overrun_alerted"]
        _shared_data["consecutive_session_overruns"] = 0
        _shared_data["session_overrun_alerted"] = False
        return was_alerted
