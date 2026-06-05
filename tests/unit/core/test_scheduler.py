from datetime import UTC, datetime

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
