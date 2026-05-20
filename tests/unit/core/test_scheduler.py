from datetime import UTC, datetime

import core.database as db
import core.runtime as runtime
import core.scheduler as scheduler


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
