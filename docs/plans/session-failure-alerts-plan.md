# Session-failure alerting implementation plan

> **For agentic workers:** Implement this plan task-by-task using TDD (write the failing test first, watch it fail, then implement). Steps use checkbox (`- [ ]`) syntax for tracking. Every code block is complete and paste-ready — no ellipsis.

**Goal:** Send exactly one Telegram alert when trading sessions start failing continuously (a configurable streak of `N` consecutive `failed` sessions), and exactly one recovery message when they resume — without one message per failed session.

**Spec:** [`../specs/2026-07-03-session-failure-alerts-design.md`](../specs/2026-07-03-session-failure-alerts-design.md)

**Branch:** `feat/session-failure-alerts` (already created).

**Tech stack:** Python 3.12, FastAPI, APScheduler, pytest. Telegram delivery via `core/logging.py` (`to_telegram=True` → HTTP POST to the telegram service, DB-independent).

## Commands (run from repo root; `PYTHONPATH=.` required)

- Single test: `venv/Scripts/python.exe -m pytest tests/unit/path::test_name -v --no-cov`
- Full unit suite: `venv/Scripts/python.exe -m pytest tests/unit/`
- Lint + format: `venv/Scripts/python.exe -m ruff check . && venv/Scripts/python.exe -m ruff format --check .`

## File structure

**Modified files:**
- `core/config.py` — add `SESSION_FAILURE_ALERT_THRESHOLD`.
- `core/runtime.py` — streak state + `register_session_failure`, `register_session_success`.
- `core/scheduler.py` — `failure_reason` capture + `_notify_session_outcome`, called from `finally`.
- `docs/configuration.md`, `.env.example`, `CLAUDE.md` — document the new env var.

**Modified tests:**
- `tests/unit/core/test_runtime.py`
- `tests/unit/core/test_scheduler.py`

---

## Task 1 — Config threshold

Add the tunable, flooring at 1 so it is always meaningful. Mirrors the `MAX_CONCURRENT_JOBS` pattern.

- [ ] **Step 1.1: add the constant in `core/config.py`**

Insert directly after the `MAX_CONCURRENT_JOBS` line (currently `core/config.py:34`):

```python
SESSION_FAILURE_ALERT_THRESHOLD = max(1, int(os.getenv("SESSION_FAILURE_ALERT_THRESHOLD", "3")))
```

**Commit:** `feat(config): add SESSION_FAILURE_ALERT_THRESHOLD tunable`

---

## Task 2 — Runtime streak state machine

The edge-triggered core. Pure, in-memory, guarded by the existing `_lock`. TDD this fully.

- [ ] **Step 2.1: write the failing tests in `tests/unit/core/test_runtime.py`**

Append:

```python
def test_register_session_failure_alerts_once_at_threshold():
    runtime._shared_data["consecutive_session_failures"] = 0
    runtime._shared_data["session_failure_alerted"] = False

    assert runtime.register_session_failure(3) is None  # 1st
    assert runtime.register_session_failure(3) is None  # 2nd
    assert runtime.register_session_failure(3) == 3  # 3rd → alert, returns count
    assert runtime.register_session_failure(3) is None  # 4th → already alerted
    assert runtime.register_session_failure(3) is None  # 5th → still silent


def test_register_session_success_signals_recovery_only_when_alerted():
    runtime._shared_data["consecutive_session_failures"] = 0
    runtime._shared_data["session_failure_alerted"] = False

    # No prior alert → no recovery signal, streak reset.
    runtime.register_session_failure(3)
    assert runtime.register_session_success() is False
    assert runtime._shared_data["consecutive_session_failures"] == 0

    # Reach the alert state, then recover once.
    runtime.register_session_failure(1)
    assert runtime.register_session_success() is True
    assert runtime._shared_data["session_failure_alerted"] is False
    # A second success without a new alert does not re-signal.
    assert runtime.register_session_success() is False
```

Confirm both fail with `AttributeError` (functions missing).

- [ ] **Step 2.2: implement in `core/runtime.py`**

Add the two keys to `_shared_data` (inside the dict literal, alongside `"config_dirty"`):

```python
    "consecutive_session_failures": 0,
    "session_failure_alerted": False,
```

Append the two functions at module end:

```python
def register_session_failure(threshold: int) -> int | None:
    """Count a failed session. Return the streak count ONCE — on the tick it
    first reaches ``threshold`` — and None otherwise, so the caller alerts
    exactly once per degraded episode."""
    with _lock:
        _shared_data["consecutive_session_failures"] += 1
        count = _shared_data["consecutive_session_failures"]
        if count >= threshold and not _shared_data["session_failure_alerted"]:
            _shared_data["session_failure_alerted"] = True
            return count
        return None


def register_session_success() -> bool:
    """Reset the failure streak. Return True if we were in the alerted state, so
    the caller sends a single recovery message."""
    with _lock:
        was_alerted = _shared_data["session_failure_alerted"]
        _shared_data["consecutive_session_failures"] = 0
        _shared_data["session_failure_alerted"] = False
        return was_alerted
```

Run the two tests → green. Run the full `test_runtime.py` → green.

**Commit:** `feat(runtime): track consecutive session-failure streak for alerting`

---

## Task 3 — Scheduler wiring

Capture a failure reason and emit the edge-triggered notifications from the `finally` block, before `finalize_session` and outside the `session_id` guard so the alert fires even when the DB is down.

- [ ] **Step 3.1: write the failing tests in `tests/unit/core/test_scheduler.py`**

Append (adjust the `import` line only if `scheduler`/`runtime` are not already imported in the file — reuse the existing imports):

```python
def test_notify_session_outcome_alerts_once_on_failure_streak(monkeypatch):
    monkeypatch.setattr(scheduler, "SESSION_FAILURE_ALERT_THRESHOLD", 3)
    runtime._shared_data["consecutive_session_failures"] = 0
    runtime._shared_data["session_failure_alerted"] = False
    sent: list[tuple[str, bool]] = []
    monkeypatch.setattr(scheduler.logging, "error", lambda msg, to_telegram=False: sent.append((msg, to_telegram)))
    monkeypatch.setattr(scheduler.logging, "info", lambda msg, to_telegram=False: sent.append((msg, to_telegram)))

    for _ in range(5):
        scheduler._notify_session_outcome("failed", "could not fetch balance")

    telegram_msgs = [m for m, tg in sent if tg]
    assert len(telegram_msgs) == 1
    assert "3" in telegram_msgs[0]
    assert "could not fetch balance" in telegram_msgs[0]


def test_notify_session_outcome_sends_single_recovery(monkeypatch):
    monkeypatch.setattr(scheduler, "SESSION_FAILURE_ALERT_THRESHOLD", 1)
    runtime._shared_data["consecutive_session_failures"] = 0
    runtime._shared_data["session_failure_alerted"] = False
    sent: list[tuple[str, bool]] = []
    monkeypatch.setattr(scheduler.logging, "error", lambda msg, to_telegram=False: sent.append((msg, to_telegram)))
    monkeypatch.setattr(scheduler.logging, "info", lambda msg, to_telegram=False: sent.append((msg, to_telegram)))

    scheduler._notify_session_outcome("failed", "boom")  # alert
    scheduler._notify_session_outcome("completed", None)  # recovery
    scheduler._notify_session_outcome("completed", None)  # no repeat

    telegram_msgs = [m for m, tg in sent if tg]
    assert len(telegram_msgs) == 2
    assert "recovered" in telegram_msgs[1].lower()


def test_notify_session_outcome_paused_is_neutral(monkeypatch):
    monkeypatch.setattr(scheduler, "SESSION_FAILURE_ALERT_THRESHOLD", 1)
    runtime._shared_data["consecutive_session_failures"] = 0
    runtime._shared_data["session_failure_alerted"] = False
    sent: list[tuple[str, bool]] = []
    monkeypatch.setattr(scheduler.logging, "error", lambda msg, to_telegram=False: sent.append((msg, to_telegram)))
    monkeypatch.setattr(scheduler.logging, "info", lambda msg, to_telegram=False: sent.append((msg, to_telegram)))

    scheduler._notify_session_outcome("paused", None)

    assert sent == []
    assert runtime._shared_data["consecutive_session_failures"] == 0
```

Confirm they fail with `AttributeError` (`_notify_session_outcome` missing).

- [ ] **Step 3.2: implement in `core/scheduler.py`**

Add the import of the threshold. Change the config import line (currently `core/scheduler.py:10`):

```python
from core.config import PAIRS, PARAM_SESSIONS, SESSION_FAILURE_ALERT_THRESHOLD, TRADING_ENABLED
```

Add the helper directly above `def trading_session() -> None:`:

```python
def _notify_session_outcome(status: str, reason: str | None) -> None:
    """Edge-triggered Telegram alerting: one message when the failure streak
    reaches the threshold, one when sessions recover. ``paused`` is neutral.
    Uses only in-memory runtime state and the (DB-independent, error-swallowing)
    Telegram logger, so it never masks the session's own exception."""
    if status == "completed":
        if runtime.register_session_success():
            logging.info("✅ Trading sessions recovered; data is updating again.", to_telegram=True)
    elif status == "failed":
        count = runtime.register_session_failure(SESSION_FAILURE_ALERT_THRESHOLD)
        if count is not None:
            detail = f" Last error: {reason}." if reason else ""
            logging.error(
                f"⚠️ {count} trading sessions have failed in a row.{detail} "
                "Prices and positions are not being updated.",
                to_telegram=True,
            )
```

Inside `trading_session`, add a `failure_reason` local next to the other initialisations (after `status = "failed"  # ...`):

```python
    failure_reason: str | None = None
```

Populate it at each failure point. The balance branch:

```python
        current_balance = call_with_retry(get_balance)
        if current_balance is None:
            failure_reason = "could not fetch balance"
            logging.error("Could not fetch balance. Skipping session.\n")
            return
```

The prices branch:

```python
        last_prices = call_with_retry(get_last_prices, PAIRS)
        if last_prices is None:
            failure_reason = "could not fetch prices"
            logging.error("Could not fetch prices. Skipping session.\n")
            return
```

The exception handler:

```python
    except Exception as exc:
        logging.exception("Unhandled exception in trading_session")
        status = "failed"
        failure_reason = failure_reason or f"unhandled exception: {exc}"
        raise
```

Call the helper in the `finally`, after `removeHandler` and before `finalize_session`:

```python
    finally:
        app_logger.removeHandler(collector)
        _notify_session_outcome(status, failure_reason)
        if session_id is not None:
            db.finalize_session(
                session_id=session_id,
                ended_at=now_utc(),
                status=status,
                balance=current_balance,
                pair_data=pair_data,
                log_messages="\n".join(collector.lines) or None,
            )
```

Run the three scheduler tests → green. Run the full `test_scheduler.py` → green.

**Commit:** `feat(scheduler): alert on sustained session-failure streaks via Telegram`

---

## Task 4 — Documentation

- [ ] **Step 4.1: `.env.example`** — add after the `MAX_CONCURRENT_JOBS=1` line:

```
SESSION_FAILURE_ALERT_THRESHOLD=3
```

- [ ] **Step 4.2: `docs/configuration.md`** — add a row to the "Bot behaviour" table:

```
| `SESSION_FAILURE_ALERT_THRESHOLD` | no | `3` | Consecutive failed sessions before one Telegram alert is sent; a single recovery message follows the next successful session. Floors at 1. |
```

- [ ] **Step 4.3: `CLAUDE.md`** — in the Configuration section, add a one-line note that `SESSION_FAILURE_ALERT_THRESHOLD` (default 3) controls the edge-triggered session-failure Telegram alert.

**Commit:** `docs: document SESSION_FAILURE_ALERT_THRESHOLD`

---

## Execution order (commits)

1. `feat(config): add SESSION_FAILURE_ALERT_THRESHOLD tunable`
2. `feat(runtime): track consecutive session-failure streak for alerting`
3. `feat(scheduler): alert on sustained session-failure streaks via Telegram`
4. `docs: document SESSION_FAILURE_ALERT_THRESHOLD`

## Acceptance checklist

- [ ] `venv/Scripts/python.exe -m pytest tests/unit/` — passes, coverage ≥ 80%.
- [ ] `venv/Scripts/python.exe -m ruff check .` — exits 0.
- [ ] `venv/Scripts/python.exe -m ruff format --check .` — exits 0.
- [ ] `grep -n "SESSION_FAILURE_ALERT_THRESHOLD" core/config.py core/scheduler.py .env.example docs/configuration.md` — returns matches in all four.
- [ ] New tests present: `test_register_session_failure_alerts_once_at_threshold`, `test_register_session_success_signals_recovery_only_when_alerted`, `test_notify_session_outcome_alerts_once_on_failure_streak`, `test_notify_session_outcome_sends_single_recovery`, `test_notify_session_outcome_paused_is_neutral`.

## Non-goals for this phase

- APScheduler event listeners (unnecessary once the hang class is closed; `finally` always runs with a definitive status).
- Persisting the streak across restarts (an in-memory reset on restart is the intended behaviour).
- Rate-limited or multi-tier alert severities (one alert + one recovery only).
- A global stop-loss or any trading-behaviour change (invariant: the trailing stop is the only exit).
