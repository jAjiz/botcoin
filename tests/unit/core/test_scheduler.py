from datetime import UTC, datetime

import pytest

import core.database as db
import core.runtime as runtime
import core.scheduler as scheduler

# TODO: branches of the per-pair loop still uncovered (extend _setup_one_pair_loop):
#   - price or ATR is None -> pair is skipped, no state change
#   - is_closing_complete True -> save_closed_position + delete_trailing_state, pair dropped
#   - is_open True -> tick_position is called
#   - state changed vs previous -> save_trailing_state; became None -> delete_trailing_state
#   - get_last_prices returning None -> session aborts with "Could not fetch prices"


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


def test_trading_session_passes_elapsed_to_notify(monkeypatch):
    monkeypatch.setattr(scheduler, "now_utc", lambda: datetime(2026, 5, 12, 10, 0, 0, tzinfo=UTC))
    monkeypatch.setattr(db, "get_bot_paused", lambda: False)
    monkeypatch.setattr(scheduler, "get_balance", lambda: {"EUR": "100"})
    monkeypatch.setattr(scheduler, "get_last_prices", lambda _pairs: {})
    monkeypatch.setattr(runtime, "update_balance", lambda _b: None)
    monkeypatch.setattr(runtime, "update_last_run_at", lambda _ts: None)
    monkeypatch.setattr(scheduler, "PAIRS", [])
    _patch_finalize(monkeypatch)
    captured: list[tuple] = []
    monkeypatch.setattr(
        scheduler, "_notify_session_outcome", lambda status, reason, elapsed: captured.append((status, reason, elapsed))
    )

    scheduler.trading_session()

    # Constant clock -> elapsed 0.0, but the (status, reason, elapsed) wiring is exercised.
    assert captured == [("completed", None, 0.0)]


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


def test_trading_session_reprices_closing_order_and_does_not_tick(monkeypatch):
    _setup_one_pair_loop(
        monkeypatch,
        trailing_state={"side": "sell", "entry_price": 50000.0, "closing_order_id": "ORD001"},
    )
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", True)
    monkeypatch.setattr(scheduler, "is_closing_complete", lambda _s: False)
    repriced: list = []
    monkeypatch.setattr(scheduler, "reprice_closing_order", lambda *a, **k: repriced.append(a))
    monkeypatch.setattr(scheduler, "tick_position", lambda *a, **k: pytest.fail("must not tick a closing position"))
    monkeypatch.setattr(scheduler, "create_position", lambda *a, **k: pytest.fail("must not create a new position"))
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    assert len(repriced) == 1
    assert repriced[0][0] == "XBTEUR"
    assert calls[0]["status"] == "completed"


def test_trading_session_does_not_reprice_when_close_completed(monkeypatch):
    _setup_one_pair_loop(
        monkeypatch,
        trailing_state={"side": "sell", "entry_price": 50000.0, "closing_order_id": "ORD001"},
    )
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", True)
    monkeypatch.setattr(scheduler, "is_closing_complete", lambda _s: True)
    monkeypatch.setattr(db, "record_position_closed", lambda *a, **k: None)
    monkeypatch.setattr(db, "delete_trailing_state", lambda _p: True)
    monkeypatch.setattr(
        scheduler, "reprice_closing_order", lambda *a, **k: pytest.fail("must not reprice a completed close")
    )
    monkeypatch.setattr(scheduler, "create_position", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "is_open", lambda _s: False)
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    assert calls[0]["status"] == "completed"


def test_trading_session_persists_a_closing_order_placed_before_a_failure(monkeypatch):
    """A5: the persist lives in a `finally`, so an order placed just before the
    pair blows up still reaches the DB — otherwise it lives on at Kraken and the
    bot has lost its id."""
    stored = {"side": "sell", "entry_price": 50000.0, "closing_order_id": "ORD001"}
    _setup_one_pair_loop(monkeypatch, trailing_state=stored)
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", True)
    monkeypatch.setattr(scheduler, "is_closing_complete", lambda _s: False)
    monkeypatch.setattr(scheduler, "create_position", lambda *a, **k: None)

    def _reprice(_pair, pos, _prices):
        pos["closing_order_id"] = "ORD002"

    def _explode(_s):
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "reprice_closing_order", _reprice)
    monkeypatch.setattr(scheduler, "is_open", _explode)
    saved: list[tuple[str, dict]] = []
    monkeypatch.setattr(db, "save_trailing_state", lambda pair, pos: saved.append((pair, dict(pos))))
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    assert saved == [("XBTEUR", {"side": "sell", "entry_price": 50000.0, "closing_order_id": "ORD002"})]
    assert calls[0]["status"] == "failed"


def test_trading_session_marks_the_pair_failed_when_the_persist_fails(monkeypatch):
    stored = {"side": "sell", "entry_price": 50000.0, "closing_order_id": "ORD001"}
    _setup_one_pair_loop(monkeypatch, trailing_state=stored)
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", True)
    monkeypatch.setattr(scheduler, "is_closing_complete", lambda _s: False)
    monkeypatch.setattr(scheduler, "is_open", lambda _s: False)
    monkeypatch.setattr(scheduler, "create_position", lambda *a, **k: None)

    def _reprice(_pair, pos, _prices):
        pos["closing_order_id"] = "ORD002"

    def _failing_save(_pair, _pos):
        raise Exception("db unavailable")

    monkeypatch.setattr(scheduler, "reprice_closing_order", _reprice)
    monkeypatch.setattr(db, "save_trailing_state", _failing_save)
    calls = _patch_finalize(monkeypatch)

    # Must not escape the loop: the other pairs still need their tick.
    scheduler.trading_session()

    assert calls[0]["status"] == "failed"
    assert "XBTEUR" in calls[0]["log_messages"]


def test_trading_session_does_not_persist_an_unchanged_pair(monkeypatch):
    stored = {"side": "sell", "entry_price": 50000.0}
    _setup_one_pair_loop(monkeypatch, trailing_state=stored)
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", True)
    monkeypatch.setattr(scheduler, "is_closing_complete", lambda _s: False)
    monkeypatch.setattr(scheduler, "is_open", lambda _s: True)
    monkeypatch.setattr(scheduler, "tick_position", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "create_position", lambda *a, **k: None)
    monkeypatch.setattr(db, "save_trailing_state", lambda *a, **k: pytest.fail("must not write an unchanged state"))
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    assert calls[0]["status"] == "completed"


@pytest.mark.parametrize("missing", ["price", "atr"])
def test_trading_session_fails_the_session_when_a_pair_has_no_price_or_atr(monkeypatch, missing):
    """An unpriced pair is an unmanaged pair (its trailing stop is frozen), so it
    must reach the consecutive-failure alert instead of passing as completed."""
    _setup_one_pair_loop(monkeypatch)
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", False)
    if missing == "price":
        monkeypatch.setattr(scheduler, "get_last_prices", lambda _pairs: {"OTHEREUR": 1.0})
    else:
        monkeypatch.setattr(scheduler, "get_current_atr", lambda _pair: None)
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    assert calls[0]["status"] == "failed"


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


def _setup_two_pair_loop(monkeypatch, *, failing_pair: str = "AAAEUR") -> None:
    """Patch the per-pair loop collaborators for two pairs, one of which raises
    inside the loop body (via get_volatility_level) while the other completes
    normally. TRADING_ENABLED is left False so the failure/success is isolated
    to the market-data recording portion of the loop body."""
    monkeypatch.setattr(scheduler, "now_utc", lambda: datetime(2026, 5, 12, 10, 0, 0, tzinfo=UTC))
    monkeypatch.setattr(db, "get_bot_paused", lambda: False)
    monkeypatch.setattr(scheduler, "get_balance", lambda: {"ZEUR": "100"})
    monkeypatch.setattr(scheduler, "get_last_prices", lambda _pairs: {"AAAEUR": 100.0, "BBBEUR": 200.0})
    monkeypatch.setattr(scheduler, "get_current_atr", lambda _pair: 10.0)
    monkeypatch.setattr(scheduler, "calculate_trading_parameters", lambda _pair: None)

    def _vol_level(pair, _atr):
        if pair == failing_pair:
            raise RuntimeError("boom")
        return "MV"

    monkeypatch.setattr(scheduler, "get_volatility_level", _vol_level)
    monkeypatch.setattr(runtime, "update_balance", lambda _b: None)
    monkeypatch.setattr(runtime, "update_pair_data", lambda *a, **k: None)
    monkeypatch.setattr(runtime, "update_last_run_at", lambda _ts: None)
    monkeypatch.setattr(db, "load_trailing_state", lambda _pair: None)
    monkeypatch.setattr(scheduler, "PAIRS", ["AAAEUR", "BBBEUR"])
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", False)


def test_trading_session_isolates_a_failing_pair_and_still_processes_the_next(monkeypatch):
    _setup_two_pair_loop(monkeypatch, failing_pair="AAAEUR")
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    final = calls[0]
    assert final["status"] == "failed"
    # The failing pair never reached the pair_data write; the healthy one did.
    assert "AAAEUR" not in final["pair_data"]
    assert final["pair_data"]["BBBEUR"]["volatility_level"] == "MV"
    assert "Error processing AAAEUR" in final["log_messages"]


def test_trading_session_reports_failed_status_with_reason_on_pair_error(monkeypatch):
    _setup_two_pair_loop(monkeypatch, failing_pair="AAAEUR")
    _patch_finalize(monkeypatch)
    captured: list[tuple] = []
    monkeypatch.setattr(
        scheduler, "_notify_session_outcome", lambda status, reason, elapsed: captured.append((status, reason, elapsed))
    )

    scheduler.trading_session()

    assert captured[0][0] == "failed"
    assert captured[0][1] == "pair errors: AAAEUR"


def test_trading_session_completes_when_no_pair_fails(monkeypatch):
    # Sanity check that the new status logic doesn't regress the all-healthy path.
    _setup_two_pair_loop(monkeypatch, failing_pair="__none__")
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    final = calls[0]
    assert final["status"] == "completed"
    assert set(final["pair_data"].keys()) == {"AAAEUR", "BBBEUR"}


def test_trading_session_isolates_pair_when_load_trailing_state_raises(monkeypatch):
    """A load_trailing_state error must fail only the pair that hit it."""
    _setup_two_pair_loop(monkeypatch, failing_pair="__none__")

    def _load_trailing_state(pair):
        if pair == "AAAEUR":
            raise Exception("DB error")
        return None

    monkeypatch.setattr(db, "load_trailing_state", _load_trailing_state)
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    final = calls[0]
    assert final["status"] == "failed"
    assert "AAAEUR" not in final["pair_data"]
    assert final["pair_data"]["BBBEUR"]["volatility_level"] == "MV"


def _reset_alert_state() -> None:
    runtime._shared_data["consecutive_session_failures"] = 0
    runtime._shared_data["session_failure_alerted"] = False
    runtime._shared_data["consecutive_session_overruns"] = 0
    runtime._shared_data["session_overrun_alerted"] = False


def _capture_telegram(monkeypatch) -> list[tuple[str, bool]]:
    sent: list[tuple[str, bool]] = []
    monkeypatch.setattr(scheduler.logging, "error", lambda msg, to_telegram=False: sent.append((msg, to_telegram)))
    monkeypatch.setattr(scheduler.logging, "info", lambda msg, to_telegram=False: sent.append((msg, to_telegram)))
    return sent


def test_notify_session_outcome_alerts_once_on_failure_streak(monkeypatch):
    monkeypatch.setattr(scheduler, "SESSION_FAILURE_ALERT_THRESHOLD", 3)
    _reset_alert_state()
    sent = _capture_telegram(monkeypatch)

    for _ in range(5):
        scheduler._notify_session_outcome("failed", "could not fetch balance", 1.0)

    telegram_msgs = [m for m, tg in sent if tg]
    assert len(telegram_msgs) == 1
    assert "3" in telegram_msgs[0]
    assert "could not fetch balance" in telegram_msgs[0]


def test_notify_session_outcome_sends_single_recovery(monkeypatch):
    monkeypatch.setattr(scheduler, "SESSION_FAILURE_ALERT_THRESHOLD", 1)
    _reset_alert_state()
    sent = _capture_telegram(monkeypatch)

    scheduler._notify_session_outcome("failed", "boom", 1.0)  # alert
    scheduler._notify_session_outcome("completed", None, 1.0)  # recovery
    scheduler._notify_session_outcome("completed", None, 1.0)  # no repeat

    telegram_msgs = [m for m, tg in sent if tg]
    assert len(telegram_msgs) == 2
    assert "recovered" in telegram_msgs[1].lower()


def test_notify_session_outcome_paused_is_neutral(monkeypatch):
    monkeypatch.setattr(scheduler, "SESSION_FAILURE_ALERT_THRESHOLD", 1)
    _reset_alert_state()
    sent = _capture_telegram(monkeypatch)

    scheduler._notify_session_outcome("paused", None, 1.0)

    assert sent == []
    assert runtime._shared_data["consecutive_session_failures"] == 0


def test_notify_session_outcome_alerts_once_on_overrun_streak(monkeypatch):
    monkeypatch.setattr(scheduler, "SESSION_FAILURE_ALERT_THRESHOLD", 3)
    monkeypatch.setattr(scheduler, "SLEEPING_INTERVAL", 60)
    _reset_alert_state()
    sent = _capture_telegram(monkeypatch)

    for _ in range(5):
        scheduler._notify_session_outcome("completed", None, 90.0)  # each overran the 60s interval

    telegram_msgs = [m for m, tg in sent if tg]
    assert len(telegram_msgs) == 1
    assert "3" in telegram_msgs[0]
    assert "60" in telegram_msgs[0]  # mentions the interval


def test_notify_session_outcome_sends_single_overrun_recovery(monkeypatch):
    monkeypatch.setattr(scheduler, "SESSION_FAILURE_ALERT_THRESHOLD", 1)
    monkeypatch.setattr(scheduler, "SLEEPING_INTERVAL", 60)
    _reset_alert_state()
    sent = _capture_telegram(monkeypatch)

    scheduler._notify_session_outcome("completed", None, 90.0)  # overran -> alert
    scheduler._notify_session_outcome("completed", None, 5.0)  # on time -> recovery
    scheduler._notify_session_outcome("completed", None, 5.0)  # no repeat

    telegram_msgs = [m for m, tg in sent if tg]
    assert len(telegram_msgs) == 2
    assert "normal" in telegram_msgs[1].lower()


def test_notify_session_outcome_failed_does_not_count_as_overrun(monkeypatch):
    monkeypatch.setattr(scheduler, "SESSION_FAILURE_ALERT_THRESHOLD", 3)
    monkeypatch.setattr(scheduler, "SLEEPING_INTERVAL", 60)
    _reset_alert_state()
    _capture_telegram(monkeypatch)

    # A failed session that also took a long time must not advance the overrun streak.
    scheduler._notify_session_outcome("failed", "boom", 999.0)

    assert runtime._shared_data["consecutive_session_overruns"] == 0
    assert runtime._shared_data["session_overrun_alerted"] is False


def test_notify_session_outcome_suppresses_overrun_recovery_when_failure_recovers(monkeypatch):
    monkeypatch.setattr(scheduler, "SESSION_FAILURE_ALERT_THRESHOLD", 3)
    monkeypatch.setattr(scheduler, "SLEEPING_INTERVAL", 60)
    _reset_alert_state()
    # Box was both failing and overrunning: both alerts are active.
    runtime._shared_data["session_failure_alerted"] = True
    runtime._shared_data["consecutive_session_failures"] = 3
    runtime._shared_data["session_overrun_alerted"] = True
    runtime._shared_data["consecutive_session_overruns"] = 3
    sent = _capture_telegram(monkeypatch)

    # First healthy on-time session: only the failure-recovery message should fire.
    scheduler._notify_session_outcome("completed", None, 5.0)

    telegram_msgs = [m for m, tg in sent if tg]
    assert len(telegram_msgs) == 1
    assert "recovered" in telegram_msgs[0].lower()
    # Overrun state is still reset even though its recovery message was suppressed.
    assert runtime._shared_data["consecutive_session_overruns"] == 0
    assert runtime._shared_data["session_overrun_alerted"] is False
