# Strategy Review Follow-ups — Design

**Date:** 2026-08-20
**Status:** Planned
**Supersedes:** the 2026-07-06 trading-strategy review, which lived at
`docs/specs/trading-strategy-review.md` and is preserved in git history. Its
findings either became one of the six items below, moved into
[`../trading-strategy.md`](../trading-strategy.md) (§5), or were dropped.

## Background

The 2026-07-06 review examined the strategy's economic logic rather than its code
quality. Its central finding shapes three of the six items here: the system is
better understood as a **portfolio rebalancer with a timing layer** than as a
trailing-stop strategy. The inventory layer decides direction and size from
allocation drift; a "position" executes nothing — it is a pending rebalance plan,
and the closing order is the only real exchange order in its whole life; the
trailing layer decides *when* to execute that plan.

Two consequences the current documentation does not state, and which §2, §3 and
§5 exist to address:

1. **`pnl_percent` is timing alpha, not economic profit.** It measures the
   execution price against the price when the plan was created — how much better
   the trailing layer did than rebalancing immediately. It is an honest metric of
   the timing layer and says nothing about whether the portfolio is growing.
2. `entry_price` is a **reference**, not a cost basis. Nothing was ever bought at
   it. Any reading of `pnl_percent` as "profit on a trade" is wrong.

## Scope

| § | Item | Changes trading behaviour |
|---|---|---|
| 1 | Exchange-synchronized order amounts and `ordermin` | No — state/boundary correctness |
| 2 | Record the real fee, net it into `pnl_percent` | No — measurement |
| 3 | Cumulative PnL in EUR | No — Grafana SQL only |
| 4 | Relative-ATR volatility classification | **Yes** |
| 5 | Strategy documentation | No |
| 6 | Documentation consolidation | No |

Items the review raised that are deliberately **not** here: bounding the
K_ACT↔K_STOP per-trade loss floor, the chop-bleed regime filter (its own deferred
backlog card), making `fee_pct` non-optional in the optimizer/backtest, and the
portfolio-vs-hold benchmark (deferred — see §3).

---

## 1. Exchange-synchronized order amounts and `ordermin`

### Problem

`place_limit_order` formats `price` and `volume` to the pair's Kraken precision
and sends those strings, but returns only the txid (`exchange/kraken.py:230-231`).
The caller never learns what was actually submitted, so `pos["volume"]` keeps the
unrounded float and drifts from the order resting at Kraken by up to one lot tick
— and that drifted value is what reaches `trailing_state.volume` and
`closed_positions.volume`.

This has already caused one production defect: sizing a reprice replacement from
`pos["volume"]` turned a fully executed order into an unplaceable dust remainder,
so a finished trade was never recorded (fixed in #64 by sizing from the order's
own `vol - vol_exec`). That fix removed the drift from *one* path by reading the
exchange's numbers back; this item removes the cause.

Separately, `build_pairs_map` captures `pair_decimals`, `lot_decimals` and
`cost_decimals` from `AssetPairs` but **not** `ordermin`
(`exchange/kraken.py:82-84`). `MIN_VALUE` (10€) does not guarantee Kraken's
per-pair minimum, which varies by asset. An order below it is rejected on every
tick — and because the pair's failure streak is what alerts, the symptom is a
latched pair, not a clear error.

The two are one item because they share a call site and a cause: the order
boundary does not tell its callers what the exchange will accept, or what it
received.

### Design

**Return the submitted amounts.** `place_limit_order` returns a small value
carrying the txid *and* the normalized `price`/`volume` it sent, instead of a bare
string. `close_position` and `reprice_closing_order` store those onto `pos`, so
state matches the exchange by construction rather than by a rounding convention
maintained by hand. `None` keeps its current meaning — outcome unknown, not
failed — so the Unconfirmed sub-state is unaffected.

**Capture and enforce `ordermin`.** `build_pairs_map` reads `ordermin` into
`config.PAIRS` alongside the decimals. It is enforced at both sizing points:
`calculate_position` skips creating a position whose volume is below it, and the
closing path checks before placing.

**The unplaceable owed exit.** A latched stop whose remaining volume is below
`ordermin` cannot be placed at all, and `stop_at` is irrevocable — so this needs a
stated rule rather than a discovered one. The rule is the one `refresh_position`
already applies at `MIN_VALUE`: the position is dropped, the residual base amount
stays untracked, and the drop is logged and sent to Telegram. Dropping is
acceptable here because the residual is by definition below the exchange's minimum
tradeable size; leaving the pair latched forever is not.

### Files

`exchange/kraken.py` (return type, `build_pairs_map`),
`trading/positions_manager.py` (`close_position`, `reprice_closing_order`,
`calculate_position`, `refresh_position`).

---

## 2. Record the real fee, net it into `pnl_percent`

### Problem

Fees are invisible exactly where they decide the sign of the edge. The per-trade
timing gains are small enough that a Kraken taker fee is a material fraction of
them, and nothing records what was actually paid.

### Design

**One fee per position.** The closing order is the only real exchange order a
position ever places, so there is exactly one fill and one fee to record. This is
the non-obvious part: a reader expecting an entry fee and an exit fee will look
for a second one that does not exist.

`OrderState` gains a `fee` field, populated by `_build_order_state` from Kraken's
`fee` (quote currency, so EUR here) — it is the anti-corruption boundary, so the
field is read there and nowhere else. `closed_positions` gains a **nullable**
`fee_eur` column. Nullable is load-bearing: it is how a reader distinguishes a
gross historical row from a net one, and there is no backfill because the data was
never fetched.

`finalize_close` expresses the fee as a percentage of the **entry notional**
(`entry_price × volume`) so the units match what `pnl_percent` already measures
against, and subtracts it:

```
fee_pct     = fee_eur / (entry_price * volume) * 100
pnl_percent = gross_pnl_percent - fee_pct
```

Per the repo rule, the ORM model in `core/db/models.py` and the Alembic migration
are updated **together** — they are not auto-synced, and CI builds the schema from
migrations.

### Consequence

`pnl_percent` changes meaning at a point in time: rows before this ships are
gross, rows after are net. The discontinuity is deliberate — preferred over a
second column no consumer would read — but it must be noted in
`trading-strategy.md` (§5) so the series is never read as homogeneous.

### Files

`exchange/kraken.py`, `trading/positions_manager.py` (`finalize_close`),
`core/db/models.py`, `core/db/positions.py`, `scripts/migrations/versions/`.

---

## 3. Cumulative PnL in EUR

### Problem

The Grafana **Cumulative PnL** panel runs:

```sql
SUM(pnl_percent::float8) OVER (ORDER BY closed_at)
```

It sums raw percentages across positions of different notionals, which is not a
quantity. Two closes on one pair — +4.0% on a 15€ position and −2.0% on a 400€
position — display as **+2.0 cumulative** while the real result is **−7.40€**. The
panel can show a rising line through a losing period.

### Design

Weight each close by its notional and accumulate in EUR:

```sql
SUM(pnl_percent / 100.0 * entry_price * volume) OVER (ORDER BY closed_at)
```

Grafana SQL only — no schema change, no new writes. Every input column already
exists on `closed_positions`.

**Ordering matters with §2.** Once `pnl_percent` is net of the fee, this
expression is *already* net; subtracting `fee_eur` again would double-count it.
`fee_eur` is for attribution — how much went to Kraken — never a second
subtraction.

The **PnL per close** panel keeps showing percentages: per trade, a percentage is
the comparable figure. Only the cumulative view needs weighting.

### What this does and does not answer

It answers *is the timing layer beating immediate rebalancing?* — which is what
`pnl_percent` is defined to measure, now correctly aggregated.

It does **not** answer *is the bot beating simply holding the target allocation?*
Because `entry_price` is a plan reference rather than a cost basis, every close can
post a positive `pnl_percent` while the portfolio falls behind holding: the bot
sells an overweight into a rally, the asset keeps climbing, and the fiat sits idle.
Each trade "wins" on timing and the portfolio still loses.

Answering that needs a portfolio value time series, which does not exist —
`bot_control.latest_balance` is a snapshot overwritten every session — and needs
external deposits and withdrawals modelled, or the comparison silently lies the
first time the operator moves EUR. That is a deferred backlog card, not a panel.

### Files

`services/grafana/dashboards/botc.json`.

---

## 4. Relative-ATR volatility classification

**This changes live trading behaviour.** It is the only item here that does.

### Problem

`get_volatility_level` classifies the **absolute** ATR against percentiles of the
pair's absolute ATR history (`trading/parameters_manager.py:51-54, 111-121`). An
absolute ATR conflates price level with volatility: as a pair's price rises its ATR
rises with it at constant relative volatility, so identical market conditions drift
upward through the levels over time. The boundaries are computed over full history,
so a pair that has doubled in price spends its recent life classified `HV`/`HH`
regardless of how volatile it actually is.

### Design

Classify `ATR / close` instead of `ATR`, with the boundaries computed from the same
ratio series. A dimensionless measure is comparable across price levels and across
pairs.

**One code path.** The live classifier and the simulator change together —
`parameters_manager` and `trading/engine.py::_vol_level_from_atr` — so backtest and
optimizer keep modelling the strategy the bot actually runs. Splitting them would
reintroduce precisely the backtest↔live divergence the review flagged.

The classification point needs the close price, which today it does not receive.
`get_volatility_level` and `get_k_stop` take the ratio (or the close alongside the
ATR); the live callers already hold the current price (`core/scheduler.py:104`,
`trading/positions_manager.py:74,106`) and the engine has close in its working
frame (`trading/engine.py:204,241`).

**No migration.** The boundaries live only in `config.PAIRS` and the runtime
calibration snapshot — never in the database — so they recalculate on the next
`calculate_trading_parameters` run. Nothing stored needs converting.

**K_STOP calibration is untouched.** The K-values are already scale-free
(`K = deviation / ATR`), so only the level a given moment maps to changes, not the
distributions themselves.

### Consequences to carry

- The `atr_p20…atr_p95` fields in `core/runtime.py` and `EngineConfig` change units
  from EUR to a ratio. Rename them, or say so in their docstrings — a float named
  `atr_p20` that is no longer an ATR is a trap.
- The `PAIR_STOP_PCT_<LEVEL>` settings keep their meaning (a percentile into a K
  distribution), but the level a given moment resolves to changes, so effective stop
  distances shift. Expect different behaviour, not merely different labels.
- The optimizer search grids were derived under absolute-ATR classification (July
  2026 study). They need re-validation before any config derived under them is
  trusted.
- Existing `closed_positions` rows were classified under the old scheme. Level
  comparisons across the change are not like-for-like.

### Files

`trading/parameters_manager.py`, `trading/engine.py`, `core/runtime.py`,
`core/scheduler.py`, `trading/positions_manager.py`.

---

## 5. Strategy documentation

Three things the code does that no document states, all to
[`../trading-strategy.md`](../trading-strategy.md):

**The MIN_MARGIN guarantee.** Under MIN_MARGIN activation the activation distance
is `K_STOP × ATR + MIN_MARGIN × entry_price`, while the stop trails `K_STOP × ATR`
behind the best price seen. An activated position therefore cannot exit worse than
`MIN_MARGIN × entry_price` from entry — MIN_MARGIN is a **minimum profit floor on
activated trades**, not merely a distance parameter. Under K_ACT activation
(`K_ACT × ATR`) no such floor exists, which is why the two modes are not
interchangeable.

**The real semantics of `pnl_percent`.** Timing alpha against the plan-creation
reference, not economic profit; `entry_price` is not a cost basis; net of the Kraken
fee from §2 onward, and gross before it.

**The re-anchoring trade-off.** After a strong adverse move a plan re-anchors its
activation toward the current price and executes into the first bounce, recording a
large negative `pnl_percent` against the original reference. This is coherent for a
rebalancer — executing late beats never executing — and it is the main source of the
worst recorded per-trade numbers. Stating the trade-off stops it being read as a
defect.

Also in this pass: `trading-strategy.md:64` and `:127` still describe
`is_closing_complete`, which no longer exists (it neither queries Kraken nor clears
fields; `finalize_close` interprets an already-fetched terminal order).

---

## 6. Documentation consolidation

### Principle: one owner per fact

| Document | Owns |
|---|---|
| `CLAUDE.md` | Architecture map, invariants, design rationale (the *why*) |
| `docs/trading-strategy.md` | What the bot decides, and on what basis |
| `docs/operations.md` | Running, observing and troubleshooting it |
| `docs/configuration.md` | The environment-variable reference |

Anything stated in two places is stated in one and linked from the other.
**Shipped specs under `docs/specs/` are dated records of a decision and are not
rewritten** — `closing-state-machine-design.md` describing the old
`is_closing_complete` is correct as history, not drift.

### Duplication found

Repricing behaviour is described three times (`operations.md`,
`trading-strategy.md`, `CLAUDE.md`); the invariants list is near-verbatim in
`trading-strategy.md` and `CLAUDE.md`; per-pair alerting appears in `operations.md`
and `CLAUDE.md`; the test commands appear in `operations.md` and `CLAUDE.md`.

### Drift the duplication already caused

All verified against the current code — `docs/operations.md` is wrong on five
counts:

- §Closing order repricing says the position "keeps the old, now-canceled order id"
  and the next tick's `is_closing_complete` clears it. The opposite is true:
  `reprice_closing_order` **drops** `closing_order_id` and writes a fresh
  `closing_request_id`, which the next tick resolves via `find_order_by_cl_ord_id`.
- It promises a Telegram alert `Failed to re-place closing order after cancel.` That
  message no longer exists; the path now logs `replacement not confirmed after
  cancel; the next tick will resolve it` and does **not** notify.
- §Per-pair failure isolation says a failed pair makes the session `failed`. It is
  `pair_error` (`core/scheduler.py:156`) — a session that did its work for every
  other pair is not a failed session.
- It says the alert message includes `pair errors: <PAIR1>, <PAIR2>`. By design no
  alert carries a reason; the real text is `⚠️ [PAIR] has failed in N sessions in a
  row and is not being managed.`
- The session-overrun alert is missing entirely.

`CLAUDE.md` states that regime ER thresholds are resolved inside
`simulate_operations`. There is no ER logic anywhere in `trading/`; the sentence is
removed.

### Approach

Remove what is obsolete or wrong, then reduce each duplicated fact to a single owner
with links replacing the copies. `CLAUDE.md`'s Design choices keep the decision and a
one-line why, with the full reasoning left in the spec it came from.

---

## Sequencing

1. **§1 + §2 together.** Both touch the order boundary and `finalize_close`, and §2's
   fee arrives through the same `OrderState` that §1 extends.
2. **§3** after §2, so the panel is written against the net `pnl_percent` once rather
   than twice.
3. **§4 alone**, as its own deployable step — it is the only behaviour change, and
   isolating it keeps its effect on trading legible.
4. **§5 + §6** any time; documentation only.

## Non-goals

- A portfolio-vs-hold benchmark (deferred backlog card — needs a portfolio time
  series and external-flow modelling).
- Bounding the K_ACT↔K_STOP per-trade loss floor. A strategy change needing its own
  decision; §5 documents the asymmetry without constraining it.
- Making `fee_pct` non-optional in the optimizer/backtest. Related to §2, separate
  surface.
- Backfilling `fee_eur` for historical closes. The data was never fetched.
- Any change to the trailing stop as the only exit mechanism.

## Testing

- **§1** — the stored volume equals the submitted volume across a place and a
  reprice; a sub-`ordermin` position is not created; a sub-`ordermin` owed exit drops
  the position rather than latching it forever.
- **§2** — `finalize_close` nets the fee for both finalizable outcomes (a `CLOSED`
  order, and a `CANCELED` order that was fully executed); a missing fee leaves
  `pnl_percent` gross rather than raising.
- **§3** — no unit test; a SQL panel. Verified by comparing it against the
  hand-computed EUR total for a known set of closes.
- **§4** — the live classifier and the engine return the same level for the same
  input, which is the property that keeps backtest and production aligned.
