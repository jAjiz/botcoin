# BoTCoin – Feature Backlog

The working backlog of features for BoTCoin. Each entry is independent and
self-contained — there is no fixed delivery order. Cards are grouped by status
and kept brief: the full design and implementation steps live in the linked spec
and plan.

**Status legend:** ✅ Shipped · 📋 Planned · 💤 Deferred

---

## ✅ Shipped

### Session Resilience & Failure Alerting

Closed the scheduler-hang class by time-bounding every blocking I/O call in the
trading loop (Kraken HTTP timeout; PostgreSQL connect/keepalive/statement
timeouts), so a stalled socket can no longer freeze the single worker thread —
it raises and becomes a recoverable missed tick. Sessions left `running` by a
past crash or hang are reconciled at startup (`cleanup_orphaned_sessions`). On
top of that, edge-triggered Telegram alerting warns once after a configurable
streak of consecutive failed sessions and once again on recovery — one message
per episode, not per failed tick.

- Spec: [`specs/2026-07-03-session-failure-alerts-design.md`](specs/2026-07-03-session-failure-alerts-design.md)
- Plan: [`plans/session-failure-alerts-plan.md`](plans/session-failure-alerts-plan.md)

### Dynamic Pair Configuration

Per-pair trading parameters (`target_pct`, `hodl_pct`, `k_act`, `min_margin`,
`stop_pct_<level>`) editable at runtime via the HTTP API and Telegram, persisted
in PostgreSQL (DB-authoritative, seeded once from `.env`), with changes taking
effect on the next session without a restart. Shipped with a cleanup collapsing
`k_act`/`min_margin` from per-side to a single value per pair.

- Spec: [`specs/dynamic-pair-config-design.md`](specs/dynamic-pair-config-design.md)
- Plan: [`plans/dynamic-pair-config-plan.md`](plans/dynamic-pair-config-plan.md)

---

## 📋 Planned

### Code-Review Hardening

Fixes for the defects found in the 2026-07-06 full code review. Phase 1 removes
three failure modes that leave the bot permanently inoperative without an alert
(pivot-detection infinite loop on flat candles; canceled/expired closing orders
corrupting state; non-transactional close persistence wedging the session loop)
and adds the agreed reprice-to-market behaviour for closing orders that never
fill. Phase 2 hardens process boundaries and secret scoping (event-loop blocking
in the optimizer routes, per-service env allowlists, migration quoting). Phase 3
collects the smaller refactors (engine dedup + `itertuples`, database module
split, doc-drift corrections). No strategy changes — the trailing stop remains
the only exit.

- Spec: [`specs/code-review-hardening-design.md`](specs/code-review-hardening-design.md)
- Plan: [`plans/code-review-hardening-plan.md`](plans/code-review-hardening-plan.md)

**Phase 1 follow-ups shipped** (`fix/phase1-followups`): `load_trailing_state`
now raises on DB errors instead of returning `None`;
`record_position_closed` logs a warning when the idempotent insert is a no-op
(`rowcount == 0`); `pytest-timeout` is installed and the A1 regression test
(`test_detect_pivots_terminates_on_flat_data`) is bounded at 10s.

**Phase 1 review follow-ups shipped**: `is_closing_complete` now clears the
closing fields on *every* terminal outcome it cannot finalize, not just
`canceled`/`expired` — the old "unexpected status" branch left them set, which
froze the position forever (the status can never change again, `reprice` declines
a non-`open` order, and `is_open` stays `False`) while alerting Telegram every
tick. A pair skipped for a missing price or ATR now counts as a failed pair, so
a frozen trailing stop can no longer hide behind a `completed` session.
`reprice_closing_order` only touches an `open` order (a `pending` one is not on
the book, so cancel/replace is churn). A5's persistence moved out of
`positions_manager` into a single `_persist_pair_state` call in the scheduler's
per-pair `finally`, which is strictly stronger than the original end-of-body
save: an order placed just before an exception used to be swallowed by the
per-pair `except` and never written. `trading/` no longer imports
`core.database`.

**Deliberately deferred out of Phase 1** (recorded by the final whole-branch
review so they are not mistaken for work Phase 1 closed):

- **`cl_ord_id`-based idempotent order placement.** The real fix for two related
  exposures: (a) a fill landing in the ~1s window between `get_order_state` and
  `cancel_order` in `reprice_closing_order` means the replacement is sized at the
  full volume and over-sells by the executed amount; (b) an `AddOrder` whose
  response is lost cannot be recognised on retry. A5 shipped the narrower
  state-persistence mitigation only.
- **`closing_requested_at` now means "last reprice", not "close requested".** No
  consumer computes a staleness timeout from it today, but an operator can no
  longer see how long an exit has been chasing. Needs a separate
  `closing_first_requested_at` if a staleness timeout is ever wanted.
- **`get_order_state` is called twice per closing tick** (scheduler + inside
  `reprice_closing_order`). Harmless today — private Kraken calls are not
  rate-limited — but the `OrderState` could be passed down instead. Folded into
  Phase 3 rather than kept as a standalone item.

### Trend/Chop Regime Filter

A Choppiness Index–based regime classifier (`TREND`/`MIXED`/`CHOP`) that gates
new position entries during sideways markets while leaving the trailing-stop
exit logic untouched. Reuses the existing OHLC + ATR pipeline, no new external
dependencies. Ships in two stages — observation first (publish the regime via
API/Telegram), enforcement second (gate entries on `regime != CHOP`).

- Spec: _to be written_

### Auto-Lookback Window for K_STOP Calibration

Replace full-history K_STOP calibration with a data-driven lookback window
selected per pair via a stability sweep, so stop sizing reflects the current
volatility regime rather than the entire price history.

**Note:** the plateau heuristic needs a meaningful history range to produce a
stable signal — more than 60 days of OHLC data are required.

- Spec: _to be written_

---

*Cards move between sections as work ships or is deferred.*
