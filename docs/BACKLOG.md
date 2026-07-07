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
