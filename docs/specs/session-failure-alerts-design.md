# Session-failure alerting design

**Date:** 2026-07-03
**Branch:** `feat/session-failure-alerts`

## Problem

The Kraken and PostgreSQL hang vectors are now time-bounded, so a stalled I/O
call no longer freezes the scheduler thread — it raises, the session is marked
`failed`, and the next tick runs. The failure mode has shifted from a *silent
freeze* to *sessions that keep running but fail in a row* (e.g. Kraken down for
an hour, or a flapping DB). Today the operator only notices this by seeing that
prices/positions have stopped updating. We want a Telegram alert, but **without
flooding the chat** with one message per failed session.

## Goal

Send **exactly one** Telegram alert when trading sessions start failing
continuously, and **exactly one** recovery message when they resume — regardless
of how many sessions fail in between.

## Approach: edge-triggered alerting

Alert on the *state transition*, not on each failure. Track a consecutive-failure
streak and an "already alerted" flag in process memory:

- The streak reaching a configurable threshold `N` (healthy → degraded) fires one
  alert.
- The next successful session (degraded → healthy) fires one recovery message and
  resets the state.

A single isolated failure (one bad tick) never alerts. With `N=3` and one-minute
sessions, the alert fires after ~3 minutes of sustained degradation — a real
signal, not noise.

Because the hang class is closed, detection lives inside `trading_session`'s
`finally` block (which always runs with a definitive `status`), so **no
APScheduler event listeners are needed**.

## Components

### 1. Config — `core/config.py`

```python
SESSION_FAILURE_ALERT_THRESHOLD = max(1, int(os.getenv("SESSION_FAILURE_ALERT_THRESHOLD", "3")))
```

Same pattern as `MAX_CONCURRENT_JOBS`. `max(1, ...)` keeps the threshold
meaningful (a value < 1 would alert on the first failure or never).

### 2. State machine — `core/runtime.py`

Two new keys in `_shared_data`, guarded by the existing `_lock`:
`"consecutive_session_failures": 0` and `"session_failure_alerted": False`.

```python
def register_session_failure(threshold: int) -> int | None:
    """Count a failed session. Return the streak count ONCE — on the tick it
    first reaches `threshold` — and None otherwise, so the caller alerts exactly
    once per degraded episode."""
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

Returning the count (not a bool) lets the alert message include "N in a row" in
one atomic call. Uses `>=` so a runtime threshold change still fires correctly.

### 3. Wiring — `core/scheduler.py`

- A `failure_reason: str | None = None` local, initialised before the `try`,
  populated at each failure point:
  - balance `None` → `"could not fetch balance"`
  - prices `None` → `"could not fetch prices"`
  - unhandled exception → the exception's message.
- A helper called from the `finally` block:

```python
def _notify_session_outcome(status: str, reason: str | None) -> None:
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
    # "paused" or any other status: neutral, no-op.
```

`finally` order becomes: `removeHandler(collector)` → `_notify_session_outcome(...)`
→ `finalize_session(...)`.

**Robustness — this is the load-bearing part of the design:**

- `_notify_session_outcome` is called **before** `finalize_session` and **outside**
  the `if session_id is not None` guard. If PostgreSQL is down, `create_session`
  raises, `session_id` stays `None`, and no telemetry is written — but the alert
  still fires, because the Telegram path is HTTP and independent of the DB. This
  is exactly the case we most need to be told about.
- The helper only touches `runtime` (in-memory) and `logging` (which already
  swallows httpx errors internally), so it can never mask the session's original
  exception nor break the `finally`.

Messages are in English, matching the existing `to_telegram` messages in
`positions_manager` and `optimizer/jobs`.

## Behaviour after restart

The streak counter lives in memory, so a restart resets it to 0. Correct: a fresh
process starts counting from scratch.

## Error handling summary

| Condition | Result |
|-----------|--------|
| 1–2 failed sessions (N=3) | Counted, no message |
| 3rd consecutive failure | One `error` alert to Telegram |
| 4th…Nth consecutive failure | Counted, no further message |
| First success after alert | One `info` recovery message; state reset |
| Success with no prior alert | State reset, no message |
| `paused` session | Neutral, streak untouched |
| DB down (session_id None) | Alert still fires via HTTP |
| Telegram service unreachable | httpx error swallowed by `logging._notify` |

## Testing (TDD)

- **`core/runtime.py`**: streak increments; alert count returned exactly once at
  the threshold and `None` afterwards; `register_session_success` resets and
  returns the recovery signal exactly once; success without a prior alert returns
  `False`.
- **`core/scheduler.py`**: a run of `failed` sessions produces exactly one
  `to_telegram` alert; a following `completed` session produces exactly one
  recovery message; `paused` counts as neither. Monkeypatch `runtime` and
  `logging`, as the existing scheduler tests do.
- **`core/config.py`**: `SESSION_FAILURE_ALERT_THRESHOLD` defaults to 3 and floors
  at 1 (config-style, minimal).

## Out of scope

- APScheduler event listeners (not needed once the hang class is closed).
- Persisting the streak across restarts.
- Per-severity or rate-limited message tiers — one alert + one recovery only.

## Documentation

Add `SESSION_FAILURE_ALERT_THRESHOLD` to the env sample / README if one documents
the other tunables, and a one-line note in the CLAUDE.md configuration section.
