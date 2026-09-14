# BoTCoin – Feature Backlog

> **Closed on 2026-09-14.** The strategy was measured and does not work, so the
> project is closed and nothing is planned. This file is kept as the record of
> what shipped, and of the one card that closed the project.

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

Nothing. The project is closed.


## 💤 Deferred

Nothing. Both cards were dropped on 2026-09-14: each tuned or measured a
strategy the study had already closed.


## ❌ Closed

### The Trailing-Stop Strategy

The strategy itself, and with it the project's first goal. **Closed 2026-09-14:
measured, not abandoned.** Twenty-four avenues on XBTEUR at production fidelity,
and none beat holding the base asset out of sample — including a gate fitted with
perfect hindsight, which noise beats in four cells of four.

Over 2023-2025 the production median is −76.0 % of base asset in four operations,
0 of 105 configurations positive, against holding's +384 % in euros. A run returns
roughly `−drift × time in cash + convexity − fees`, and at those drifts the first
term dwarfs the other two. No configuration, re-anchor, frequency or fee level
changes it.

Reopening this needs data the study does not hold — order book, trades tape,
funding rates, cross-asset — not a better parameterisation. The four *Production
consequences, unimplemented* listed at the end of the study serve this closed
strategy and are not planned either.

- Study: [`specs/optimizer-validation-design.md`](specs/optimizer-validation-design.md)
