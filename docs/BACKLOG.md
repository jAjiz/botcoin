# BoTCoin – Feature Backlog

The working backlog of features for BoTCoin. Each entry is independent and
self-contained — there is no fixed delivery order. Cards are grouped by status
and kept brief: the design and the reasoning behind it live in the linked spec.
A card being implemented also links a plan, which is deleted once it ships.

**Status legend:** ✅ Shipped · 📋 Planned · 💤 Deferred · ❌ Closed



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

### Closing State Machine & Idempotent Placement

A lost `AddOrder` response used to be indistinguishable from a rejection, so the
next tick could place a second exit for the same holding. Every order now carries
a client-chosen `cl_ord_id`, and a closing position routes on whether its
placement was *confirmed* — which decides whether "Kraken doesn't have it"
licenses a re-place or means the pair is unmanaged. Landed together with a
restructure of the closing path into one selector with a single `OrderStatus`
dispatch, which also dropped the reprice tick from three `get_order_state` calls
to two.

- Spec: [`specs/closing-state-machine-design.md`](specs/closing-state-machine-design.md)

### Strategy Review Follow-ups

Six items from the 2026-07-06 trading-strategy review, in one spec: the order
boundary returns the amounts it actually submitted and captures Kraken's
`ordermin`; the real fee of each fill is recorded and netted into `pnl_percent`;
Grafana's cumulative panel switches to notional-weighted EUR, since summing raw
percentages can show a rising line through a losing period; volatility
classification moves from absolute ATR to ATR/close (the one behaviour change,
live, engine and K_STOP calibration together); the MIN_MARGIN activation margin, the real meaning of
`pnl_percent` and the re-anchoring trade-off get documented; and a consolidation
pass removes the duplication that has already left `operations.md` stale.

- Spec: [`specs/strategy-review-followups-design.md`](specs/strategy-review-followups-design.md)



## 📋 Planned



## 💤 Deferred

### Auto-Lookback Window for K_STOP Calibration

Replace full-history K_STOP calibration with a data-driven lookback window
selected per pair via a stability sweep, so stop sizing reflects the current
volatility regime rather than the entire price history.

**Note:** the plateau heuristic needs a meaningful history range to produce a
stable signal — more than 60 days of OHLC data are required.

- Spec: _to be written_

### Portfolio-vs-Hold Benchmark

Answer the question `pnl_percent` structurally cannot: is the bot beating simply
holding the target allocation? Because `entry_price` is a plan reference and not
a cost basis, every close can post a positive `pnl_percent` while the portfolio
falls behind holding — the bot sells an overweight into a rally, the asset keeps
climbing, and the fiat sits idle.

Deferred on cost, not on value: nothing records portfolio value over time
(`bot_control.latest_balance` is a snapshot overwritten every session), so this
needs a time series, and external deposits and withdrawals must be modelled or
the comparison silently lies the first time the operator moves EUR.

- Spec: _to be written_


## ❌ Closed

### Trend/Chop Regime Filter

A Choppiness Index–based regime classifier (`TREND`/`MIXED`/`CHOP`) that would
gate the bot through sustained trends, leaving the trailing-stop exit logic
untouched. **Closed 2026-09-07: bounded before it was built, and the bound says
no.**

The upper bound was measured with a perfect-hindsight oracle — the bars to gate
chosen *after* seeing what the price did, which no live classifier can match — by
masking sell exits on them across 105 configs over one continuous run of
2025-04-01 .. 2026-03-31. It is worth **+0.6 points** of accumulated base asset.
A real classifier gets some fraction of that, and the observation stage alone
costs more than the whole ceiling is worth.

The `+0.44` rank correlation that motivated this card does not stand. It came
from `regime_filter_screen.py` (deleted), measured before both engine fixes
shipped: the cash leg was still paid as if the bot held a short — worth up to
+162 points of overstatement on exactly the low-frequency configs it used — and
nothing recalibrated. Its 75 "weekly windows" over 111 days of data are about
eleven independent weeks, each a fresh restarted simulation. **Do not cite it.**

Reopening this needs a new mechanism, not a better classifier: the loss in a
rally is selling and rebuying higher, and the oracle already prices out every
way of not selling.

- Study: [`specs/optimizer-validation-design.md`](specs/optimizer-validation-design.md) § Closed avenues
