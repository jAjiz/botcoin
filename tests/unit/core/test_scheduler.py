from datetime import UTC, datetime

import pytest

import core.database as db
import core.runtime as runtime
import core.scheduler as scheduler

# TODO: the per-pair processing loop (scheduler.py lines ~83-126) is still
# uncovered — these tests only exercise the paused / early-return guards with an
# empty PAIRS list. Add tests that run the loop with one pair and a stubbed
# get_last_prices / get_current_atr, covering:
#   - price or ATR is None -> pair is skipped, no state change
#   - calculate_trading_parameters fires only when _session_count % PARAM_SESSIONS == 0
#   - is_closing_complete True -> save_closed_position + delete_trailing_state, pair dropped
#   - no trailing state -> create_position is called
#   - is_open True -> tick_position is called
#   - state changed vs previous -> save_trailing_state; state became None -> delete_trailing_state
#   - per-pair pair_data is recorded into the finalized session
#   - get_last_prices returning None -> session aborts with "Could not fetch prices"
# All collaborators (db, get_balance, get_last_prices, get_current_atr,
# create_position, tick_position, is_open, is_closing_complete) are monkeypatchable
# at the scheduler module level, as the existing tests already do.


def _patch_finalize(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(db, "create_session", lambda _started: 1)
    monkeypatch.setattr(db, "finalize_session", lambda **kwargs: calls.append(kwargs))
    return calls


def test_trading_session_records_successful_session(monkeypatch):
    fixed_now = datetime(2026, 5, 12, 10, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(scheduler, "now_utc", lambda: fixed_now)
    monkeypatch.setattr(db, "get_bot_paused", lambda: False)
    monkeypatch.setattr(scheduler, "get_balance", lambda: {"EUR": "100"})
    monkeypatch.setattr(scheduler, "get_last_prices", lambda _pairs: {})
    monkeypatch.setattr(runtime, "update_balance", lambda _b: None)
    monkeypatch.setattr(runtime, "update_last_run_at", lambda _ts: None)
    monkeypatch.setattr(scheduler, "PAIRS", [])
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    final = calls[0]
    assert final["session_id"] == 1
    assert final["status"] == "completed"
    assert final["balance"] == {"EUR": "100"}
    assert final["pair_data"] == {}
    assert "SESSION COMPLETE" in final["log_messages"]


def test_trading_session_records_paused_session(monkeypatch):
    monkeypatch.setattr(scheduler, "now_utc", lambda: datetime(2026, 5, 12, 10, 0, 0, tzinfo=UTC))
    monkeypatch.setattr(db, "get_bot_paused", lambda: True)
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    assert calls[0]["status"] == "paused"
    assert calls[0]["balance"] is None
    assert calls[0]["pair_data"] == {}


def test_trading_session_records_failed_balance_fetch(monkeypatch):
    monkeypatch.setattr(scheduler, "now_utc", lambda: datetime(2026, 5, 12, 10, 0, 0, tzinfo=UTC))
    monkeypatch.setattr(db, "get_bot_paused", lambda: False)
    monkeypatch.setattr(scheduler, "get_balance", lambda: None)
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    assert calls[0]["status"] == "failed"
    assert calls[0]["balance"] is None
    assert "Could not fetch balance" in calls[0]["log_messages"]


def _setup_one_pair_loop(monkeypatch, *, trailing_state=None):
    """Patch the per-pair loop collaborators for a single pair (XBTEUR)."""
    monkeypatch.setattr(scheduler, "now_utc", lambda: datetime(2026, 5, 12, 10, 0, 0, tzinfo=UTC))
    monkeypatch.setattr(db, "get_bot_paused", lambda: False)
    monkeypatch.setattr(scheduler, "get_balance", lambda: {"ZEUR": "100"})
    monkeypatch.setattr(scheduler, "get_last_prices", lambda _pairs: {"XBTEUR": 50000.0})
    monkeypatch.setattr(scheduler, "get_current_atr", lambda _pair: 100.0)
    monkeypatch.setattr(scheduler, "calculate_trading_parameters", lambda _pair: None)
    monkeypatch.setattr(scheduler, "get_volatility_level", lambda _pair, _atr: "MV")
    monkeypatch.setattr(runtime, "update_balance", lambda _b: None)
    monkeypatch.setattr(runtime, "update_pair_data", lambda *a, **k: None)
    monkeypatch.setattr(runtime, "update_last_run_at", lambda _ts: None)
    monkeypatch.setattr(db, "load_trailing_state", lambda _pair: trailing_state)
    monkeypatch.setattr(scheduler, "PAIRS", ["XBTEUR"])


def test_trading_session_skips_positions_when_trading_disabled(monkeypatch):
    _setup_one_pair_loop(monkeypatch)
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", False)
    monkeypatch.setattr(scheduler, "create_position", lambda *a, **k: pytest.fail("must not open positions"))
    monkeypatch.setattr(scheduler, "tick_position", lambda *a, **k: pytest.fail("must not manage positions"))
    monkeypatch.setattr(scheduler, "is_closing_complete", lambda _s: pytest.fail("must not check closes"))
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    final = calls[0]
    # Market data is still ingested and recorded; only trading is skipped.
    assert final["status"] == "completed"
    assert final["pair_data"]["XBTEUR"]["volatility_level"] == "MV"


def test_trading_session_warns_on_stored_position_when_trading_disabled(monkeypatch):
    _setup_one_pair_loop(monkeypatch, trailing_state={"pair": "XBTEUR", "entry_price": 50000.0})
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", False)
    monkeypatch.setattr(scheduler, "create_position", lambda *a, **k: pytest.fail("must not trade"))
    monkeypatch.setattr(scheduler, "tick_position", lambda *a, **k: pytest.fail("must not trade"))
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    assert calls[0]["status"] == "completed"
    assert "NOT being managed" in calls[0]["log_messages"]


def test_trading_session_opens_position_when_trading_enabled(monkeypatch):
    _setup_one_pair_loop(monkeypatch)
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", True)
    monkeypatch.setattr(scheduler, "is_closing_complete", lambda _s: False)
    monkeypatch.setattr(scheduler, "is_open", lambda _s: False)
    created: list = []
    monkeypatch.setattr(scheduler, "create_position", lambda *a, **k: created.append(a))
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    assert len(created) == 1  # no stored position -> create_position is called once
    assert calls[0]["status"] == "completed"


def test_trading_session_recalcs_params_when_config_dirty(monkeypatch):
    _setup_one_pair_loop(monkeypatch)
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", False)
    recalcs: list[str] = []
    monkeypatch.setattr(scheduler, "calculate_trading_parameters", lambda pair: recalcs.append(pair))
    # Force the counter off a PARAM_SESSIONS multiple so only the dirty flag can trigger.
    monkeypatch.setattr(scheduler, "_session_count", 1)
    monkeypatch.setattr(scheduler, "PARAM_SESSIONS", 720)
    _patch_finalize(monkeypatch)

    runtime.mark_config_dirty("XBTEUR")
    scheduler.trading_session()

    assert recalcs == ["XBTEUR"]


def test_trading_session_no_recalc_when_not_dirty_and_off_cycle(monkeypatch):
    _setup_one_pair_loop(monkeypatch)
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", False)
    recalcs: list[str] = []
    monkeypatch.setattr(scheduler, "calculate_trading_parameters", lambda pair: recalcs.append(pair))
    monkeypatch.setattr(scheduler, "_session_count", 1)
    monkeypatch.setattr(scheduler, "PARAM_SESSIONS", 720)
    _patch_finalize(monkeypatch)

    # ensure no leftover dirty flag from another test
    runtime.pop_config_dirty("XBTEUR")
    scheduler.trading_session()

    assert recalcs == []
