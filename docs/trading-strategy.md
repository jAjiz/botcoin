# Trading Strategy Reference

BoTCoin implements an ATR-based trailing-stop strategy that adapts stop distances to current market volatility. This document covers: decision logic → position lifecycle → volatility classification → K_STOP calibration.

---

## Decision logic

Every trading session, for each configured pair, the bot:

1. Fetches the current price and computes ATR from stored OHLC data.
2. Classifies the ATR into one of five volatility levels (LL / LV / MV / HV / HH) using pair-specific percentile boundaries.
3. Selects K_STOP for the current level and position side from the calibrated parameter set.
4. If no position is open, creates one with a calculated activation price.
5. If a position is open and pre-activation, monitors the activation price (recalibrating if ATR drifts).
6. If a position is active (trailing), tracks the trailing price and checks whether the stop has been hit.
7. If a closing order was placed and is now filled on Kraken, records the real fill price and computes PnL.

### Balance-majority logic

Portfolio composition determines whether a new position is a BUY or SELL:

- If the asset's current value **exceeds** `PAIR_TARGET_PCT` → prioritise SELL (reduce the overweight).
- If the asset's current value **is below** `PAIR_TARGET_PCT` → prioritise BUY (build toward the target).

The position value is the difference between the target allocation and the current allocation, capped at available EUR (buys) or available asset (sells). Positions whose computed value is below `MIN_VALUE` are skipped.

---

## Position lifecycle

### Activation price

The activation price is the trigger that converts a waiting position into an active trailing stop. Two calculation strategies are supported:

**K_ACT strategy** (when `PAIR_K_ACT` is set):
```
activation_distance = K_ACT × ATR
SELL: activation_price = entry_price + activation_distance
BUY:  activation_price = entry_price − activation_distance
```

**MIN_MARGIN strategy** (when `PAIR_K_ACT` is not set):
```
activation_distance = K_STOP × ATR + MIN_MARGIN × entry_price
```

`K_ACT` and `MIN_MARGIN` are single values per pair, shared by both sides (the earlier per-side `PAIR_SELL_K_ACT` / `PAIR_BUY_K_ACT` variants were removed). `K_STOP` remains per-side because it is derived from pivot analysis, which naturally differs between uptrends and downtrends.

Under MIN_MARGIN activation the activation distance is `K_STOP × ATR + MIN_MARGIN × entry_price`, while the stop trails `K_STOP × ATR` behind the best price seen since activation. An activated position therefore cannot exit worse than `MIN_MARGIN × entry_price` away from entry: **MIN_MARGIN is a minimum profit floor on activated trades**, not merely a distance parameter. Under K_ACT activation (`K_ACT × ATR`) no such floor exists — the stop can trail back through the entry price — which is why the two modes are not interchangeable.

### Trailing-stop mechanics

Once the market price crosses the activation price:

1. The **trailing price** tracks the best price seen since activation (highest for SELL, lowest for BUY).
2. The **stop price** is recalculated each session: `trailing_price ± K_STOP × ATR`.
3. When the market reverses and crosses the stop price, a limit order is placed at the current market price to close the position.

### Recalibration

If ATR changes by more than `ATR_DESV_LIMIT` (default 20 %) between sessions, both the activation price (pre-activation) and the stop price (post-activation) are recalculated with the new ATR. This prevents the stop from becoming stale in a volatility regime shift.

After a strong adverse move, a plan re-anchors its activation toward the current price and executes into the first bounce, recording a large negative `pnl_percent` against the original reference. This is deliberate: for a rebalancer, executing late beats never executing. It is also the main source of the worst recorded per-trade numbers, so those are the mechanism working as designed rather than a defect.

### Position closure

`close_position` places a limit order and records the approximate `closing_price` (at order placement time). `finalize_close` interprets an order the caller has already fetched and already established as terminal — it does not poll `QueryOrders` itself; `_drive_closing_order` does the fetching and the status dispatch. When the fill is confirmed, `finalize_close` overwrites `closing_price` with the real fill price and computes `pnl_percent`. PnL is valid only after `finalize_close` returns `True`.

An order that ends `canceled` or `expired` but whose executed volume covers the whole order (`vol_exec >= vol`) is finalized the same way: a cancel that raced a complete fill leaves nothing to manage, so it is recorded as a completed close rather than reopened for management. Any terminal order that still has a remainder clears its closing fields instead, and the position resumes management on the same tick.

`pnl_percent` is **timing alpha, not economic profit**. It measures the execution price against `entry_price` — the price when the rebalance plan was created — so it reports how much better the trailing layer did than rebalancing immediately. `entry_price` is a reference, not a cost basis: nothing was ever bought at it. From the fee change onward it is **net** of the real Kraken fee (recorded in `closed_positions.fee_eur`); rows closed before that are gross, so the series is not homogeneous across that point.

---

## Volatility classification

ATR is classified into five levels using percentile boundaries precomputed from each pair's OHLC history:

| Level | ATR range | Description |
|---|---|---|
| LL | < P20 | Very Low Volatility |
| LV | P20–P50 | Low Volatility |
| MV | P50–P80 | Medium Volatility |
| HV | P80–P95 | High Volatility |
| HH | > P95 | Very High Volatility |

`get_volatility_level(pair, atr)` in `trading/parameters_manager.py` performs this classification against the current pair's ATR percentile boundaries.

---

## K_STOP calibration

K_STOP is the trailing-stop coefficient: `stop_price = trailing_price ± K_STOP × ATR`. A larger K_STOP widens the stop (more tolerance for noise before closing); a smaller K_STOP tightens it.

### Structural noise analysis

`analyze_structural_noise` in `trading/market_analyzer.py` identifies pivot points (local minima and maxima) using `scipy.signal.argrelextrema`. For each trend segment it computes:

```
K = max_deviation_from_entry / ATR
```

This K-value represents how far the price moved against the dominant trend (structural noise) relative to ATR — the amount of "noise" to tolerate in a stop.

### K-value percentile selection

`calculate_k_stops` in `trading/parameters_manager.py` groups the per-segment K-values by volatility level and selects the value at the configured percentile (`PAIR_STOP_PCT_<LEVEL>`).

- **Low percentile** (e.g. P25): tight stop — higher closure frequency, smaller per-trade loss.
- **High percentile** (e.g. P95): wide stop — lower closure frequency, larger noise tolerance.

SELL positions use K-values from uptrend segments (drawdown resistance); BUY positions use K-values from downtrend segments (bounce resistance).

### Parameter refresh cadence

Parameters are recalculated every `PARAM_SESSIONS` sessions (default 720 ≈ 12 hours at 60-second intervals). The lookback window spans the entire `ohlc_data` history for the pair.

### Choosing percentile values

No universal answer exists — optimal percentiles depend on the pair's historical volatility and the operator's risk tolerance. Starting recommendations:

- Use the backtest (`trading/backtest.py`) to compare win rate and PnL at different percentile settings over historical data.
- Tighter stops (lower percentile) in high-volatility regimes are often better because ATR already provides distance.
- Looser stops (higher percentile) in low-volatility regimes prevent premature closure from small reversals.

---

## Constraints and invariants

- The trailing stop is the **only** exit mechanism. There is no global stop-loss, no max-loss-per-position, no panic kill switch. Adding one is a strategy change and must be discussed explicitly.
- A position whose stop has fired (`stop_at` latched) is **not open** — `tick_position` must not run on it, whether or not a closing order was ever placed (the scheduler enforces this via step ordering).
- `closing_price` is an estimate until fill confirmation, and it is only present once an order actually rests at Kraken: `close_position` writes it when a placement succeeds, and each `reprice_closing_order` chase overwrites it again (still an estimate, at the new limit price) while the order remains unfilled. A breach whose placement failed, or whose order died terminally without a usable fill, has `stop_at` latched and no `closing_price` at all — the exit is owed, but nothing has been quoted for it yet. `finalize_close` performs the final write with the real fill price and computes `pnl_percent`; any read before it returns `True` is reading an estimate.
- `_safe_call` in `exchange/kraken.py` swallows errors and returns `None`. Every caller that does not handle `None` will silently corrupt state.

---

## Strategy review follow-ups

The 2026-07-06 review of the strategy's economic logic and structural risks —
the real semantics of `pnl_percent` (timing alpha, not economic profit), the
MIN_MARGIN profit-floor guarantee, fee sensitivity, and the absolute-ATR
classification bias — is carried forward as a planned work item in
[`specs/strategy-review-followups-design.md`](specs/strategy-review-followups-design.md).
The review itself is in git history.
