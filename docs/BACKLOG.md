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

- Spec: [`specs/session-failure-alerts-design.md`](specs/session-failure-alerts-design.md)
- Plan: [`plans/session-failure-alerts-plan.md`](plans/session-failure-alerts-plan.md)

### Dynamic Pair Configuration

Per-pair trading parameters (`target_pct`, `hodl_pct`, `k_act`, `min_margin`,
`stop_pct_<level>`) editable at runtime via the HTTP API and Telegram, persisted
in PostgreSQL (DB-authoritative, seeded once from `.env`), with changes taking
effect on the next session without a restart. Shipped with a cleanup collapsing
`k_act`/`min_margin` from per-side to a single value per pair.

- Spec: [`specs/dynamic-pair-config-design.md`](specs/dynamic-pair-config-design.md)
- Plan: [`plans/dynamic-pair-config-plan.md`](plans/dynamic-pair-config-plan.md)

### Code-Review Hardening

Fixes for the defects found in the 2026-07-06 full code review, in three phases:
the close-lifecycle failure modes that left the bot inoperative without an alert
(1), process-boundary and secret-scoping hardening (2), and the cleanups — engine
dedup, the `core/db/` split, ISO date validation (3). No strategy changes — the
trailing stop remains the only exit. What the review raised and we parked is in
the Deferred card below.

- Spec: [`specs/code-review-hardening-design.md`](specs/code-review-hardening-design.md)
- Plan: [`plans/code-review-hardening-plan.md`](plans/code-review-hardening-plan.md)

### Stop-Latched Close

A failed `place_limit_order` used to leave no trace, so the next tick re-entered
`tick_position` and could widen the stop past the breach or re-arm the trail: an
API failure revoked a strategy decision. `stop_at` now latches the breach before
the placement attempt, `is_open` is `not stop_at`, and `manage_closing_order`
owns everything between the breach and the fill.

- Spec: [`specs/stop-latched-close-design.md`](specs/stop-latched-close-design.md)
- Plan: [`plans/stop-latched-close-plan.md`](plans/stop-latched-close-plan.md)

---

## 📋 Planned

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

### Deferred out of Code-Review Hardening

Points raised during the code review and consciously parked — recorded here so
they are not mistaken for work the hardening phases closed.

- **`cl_ord_id`-based idempotent order placement.** Orders are identified only
  by the `txid` Kraken returns, so the bot cannot ask "did *my* order land?"
  after a lost `AddOrder` response, nor size a replacement against a fill that
  landed inside the cancel/replace window. Both end with a position size the bot
  does not know about. Phase 1 (A5) shipped only the narrower guarantee that an
  id the bot *has* is never lost. Needs a spec: which client-id mechanism
  (`userref` vs `cl_ord_id`) this account tier and the krakenex path actually
  support. Sizing replacements from `vol - vol_exec` after a cancel-window fill
  is done (`reprice_closing_order` re-queries post-cancel); the `AddOrder`-loss
  half of the idempotency gap remains.
- **`get_order_state` is called three times per closing tick** (scheduler, plus
  the pre-cancel check and the post-cancel re-query inside
  `reprice_closing_order`). Private Kraken calls *are* rate-limited — a per-tier
  counter, ~15–20 with a slow decay — and only the public path is throttled
  in-process by `_wait_rate_limit`. It fits today because each pair's iteration
  includes a public OHLC call that forces ≥1 s of spacing, so the counter decays
  between pairs; a throttled call would degrade to `None` via `_safe_call`, and
  the post-cancel bail leaves the position without a resting exit for one tick.
  The scheduler's `OrderState` could be passed down to remove one of the three.

---

*Cards move between sections as work ships or is deferred.*
