# BoTCoin – Feature Backlog

The working backlog of features for BoTCoin. Each entry is independent and
self-contained — there is no fixed delivery order. Cards are grouped by status
and kept brief: the design and the reasoning behind it live in the linked spec.
A card being implemented also links a plan, which is deleted once it ships.

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

- Spec: [`specs/session-failure-alerts-design.md`](specs/session-failure-alerts-design.md)

### Dynamic Pair Configuration

Per-pair trading parameters (`target_pct`, `hodl_pct`, `k_act`, `min_margin`,
`stop_pct_<level>`) editable at runtime via the HTTP API and Telegram, persisted
in PostgreSQL (DB-authoritative, seeded once from `.env`), with changes taking
effect on the next session without a restart. Shipped with a cleanup collapsing
`k_act`/`min_margin` from per-side to a single value per pair.

- Spec: [`specs/dynamic-pair-config-design.md`](specs/dynamic-pair-config-design.md)

### Code-Review Hardening

Fixes for the defects found in the 2026-07-06 full code review, in three phases:
the close-lifecycle failure modes that left the bot inoperative without an alert
(1), process-boundary and secret-scoping hardening (2), and the cleanups — engine
dedup, the `core/db/` split, ISO date validation (3). No strategy changes — the
trailing stop remains the only exit. What the review parked is now covered by the
Closing State Machine card.

- Spec: [`specs/code-review-hardening-design.md`](specs/code-review-hardening-design.md)

### Stop-Latched Close

A failed `place_limit_order` used to leave no trace, so the next tick re-entered
`tick_position` and could widen the stop past the breach or re-arm the trail: an
API failure revoked a strategy decision. `stop_at` now latches the breach before
the placement attempt, `is_open` is `not stop_at`, and `manage_close_position`
owns everything between the breach and the fill.

- Spec: [`specs/stop-latched-close-design.md`](specs/stop-latched-close-design.md)

---

## 📋 Planned

### Closing State Machine & Idempotent Placement

A lost `AddOrder` response is today indistinguishable from a rejection, so the
next tick can place a second exit for the same holding. Every order gains a
client-chosen `cl_ord_id`, and a closing position routes on whether its placement
was *confirmed* — which decides whether "Kraken doesn't have it" licenses a
re-place or means the pair is unmanaged. Lands together with a restructure of the
closing path into one selector with a single `OrderStatus` dispatch, which also
drops the reprice tick from three `get_order_state` calls to two.

Ships in two PRs with a live check between them: the exchange behaviour the
resolver depends on has to be confirmed on the account before anything depends
on it.

- Spec: [`specs/closing-state-machine-design.md`](specs/closing-state-machine-design.md)
- Plan: [`plans/closing-state-machine-plan.md`](plans/closing-state-machine-plan.md)

### Strategy Review Follow-ups

Actionable, non-strategy items from the 2026-07-06 trading-strategy review:
record the real Kraken fee of each fill alongside `pnl_percent` and add a live
"portfolio vs holding the target allocation" benchmark (Grafana); make
`fee_pct` non-optional in the optimizer/backtest (default to the real fee tier,
add a slippage term); capture Kraken's `ordermin` in the pairs map and enforce
it when sizing/closing positions. The review's strategy-level recommendations
(relative-ATR volatility classification, bounding the K_ACT↔K_STOP loss floor)
stay in the spec as discussion items — they change trading behaviour and need
an explicit decision first.

- Spec: [`specs/trading-strategy-review.md`](specs/trading-strategy-review.md)

### Exchange-Synchronized Order Amounts

`place_limit_order` formats `price`/`volume` to the pair's Kraken precision but
returns only the txid, so callers never learn what was actually submitted.
`pos["volume"]` therefore drifts from the order resting at Kraken (and from the
`Numeric(28, 10)` column): `reprice_closing_order` stores a raw float
subtraction while a rounded value goes on the wire. Have the boundary return the
normalized amounts it sent and store those, so state matches the exchange by
construction rather than by a rounding convention kept in sync by hand.

Scope: order volumes and submitted order prices. ATR fields (`activation_atr`,
`stop_atr`) stay full precision — rounding them to `pair_decimals` degrades
ATR-drift detection on low-value pairs (see the rounding Design choice in
`CLAUDE.md`). Touches the same pairs map as the `ordermin` capture in Strategy
Review Follow-ups.

- Spec: _to be written_

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

## 💤 Deferred

### Orphan Order Sweep

A hard process kill *between* the `AddOrder` send and the tick's `finally` loses
the client id before it is persisted, so that order is unrecoverable by id. An
unfiltered `OpenOrders` sweep, matched against the ids the bot knows, would catch
it. Deliberately left out of the closing state machine spec: different mechanism,
different trigger, and its lookup cannot reuse the "always among the newest"
argument that bounds the resolver's, so it needs its own paging strategy.

- Spec: _to be written_

---

*Cards move between sections as work ships or is deferred.*
