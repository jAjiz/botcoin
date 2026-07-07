# Trading-strategy review

**Date:** 2026-07-06
**Branch:** `docs/trading-strategy-review` (documentation only)
**Scope:** the strategy itself — economic logic, edge assumptions, structural
risks. Code-quality findings live in
[`code-review-hardening-design.md`](code-review-hardening-design.md); this
review assumes those fixes land.

## What the strategy actually is

The docs describe an "ATR trailing-stop strategy", but the system is better
understood as a **portfolio rebalancer with a timing layer**:

- The inventory layer (`trading/inventory_manager.py`) decides direction and
  size purely from allocation drift: sell the excess above the HODL floor when
  overweight vs `TARGET_PCT`, buy the deficit toward the target when
  underweight, capped by real balances (no leverage, no shorting).
- A "position" (`create_position`) executes nothing — it is a **pending
  rebalance plan**. The only real exchange order in a position's whole life is
  the closing order.
- The trailing layer decides *when* to execute that plan: wait for the price to
  move in the position's favour by the activation distance, chase it with an
  ATR-scaled stop, execute on reversal.

Two consequences that the current docs do not state:

1. **`pnl_percent` is timing alpha, not economic profit.** It measures the
   execution price against the price when the plan was created — i.e. how much
   better (or worse) the trailing layer did than rebalancing immediately. It is
   a good, honest metric, but the *economic* PnL of the strategy (portfolio
   value vs holding the target allocation without trading) is not measured
   anywhere.
2. With the example allocation (`TARGET_PCT` 50+50 = 100, `HODL_PCT` 25),
   structural fiat tends to zero: the system oscillates around full investment,
   selling rallies and buying back dips. Buys only become possible after sells
   create fiat.

## Strengths

- **K_STOP is calibrated from measured noise, not picked by hand.** The stop
  coefficient comes from the empirical distribution of counter-moves inside
  historical trend segments (pivot detection → `K = counter-move / ATR`),
  bucketed per volatility level and selected at a configurable percentile
  (`trading/market_analyzer.py::analyze_structural_noise`,
  `trading/parameters_manager.py::calculate_k_stops`). Methodologically ahead of
  the usual fixed "2×ATR".
- **The MIN_MARGIN activation mode has a structural profit floor.** Activation
  distance = `K_STOP×ATR + MIN_MARGIN×price` means the stop at the moment of
  activation sits at ≈ `entry + MIN_MARGIN×entry` (sell side): the worst case
  for an *activated* trade — activate and get stopped immediately — is still a
  gross profit of ≈ `MIN_MARGIN`. This guarantee is nowhere documented and is a
  genuine differentiator vs the K_ACT mode (see weakness 1).
- **Risk of ruin is bounded by construction:** buys capped by available fiat
  (with reservations for other pending buys), sells capped by holdings above the
  HODL floor, and the HODL floor guarantees a permanent core position.
- **Re-anchoring** (`positions_manager.reanchor_activation_price`) keeps a plan
  executable after the market runs away, so the bot cannot be locked out of its
  own rebalance forever; **ATR-drift recalibration** (±`ATR_DESV_LIMIT`) keeps
  activation/stop distances current across volatility regime shifts.
- **The optimizer is unusually honest about overfitting:** candidates rank by
  `robust_pnl = min(train_pnl, test_pnl)` and AUTO convergence requires seeds to
  agree on the *configuration*, not merely on similar scores.

## Structural weaknesses (ordered by impact)

### 1. The K_ACT mode has no per-trade loss floor — and with the defaults, activated trades can lose systematically

Worst case for an activated trade is `(K_ACT − K_STOP) × ATR` (activation
reached, then stopped immediately). With the example `K_ACT = 1.2` and stop
percentile 0.90, calibrated K_STOP typically lands ≥ 1.5–3 → the floor is
**negative**: whipsaws produce realized losses per activated trade as a matter
of course. Not wrong per se — but the K_ACT↔K_STOP relationship is *the* risk
parameter of the strategy and is neither analysed nor documented. The
MIN_MARGIN mode's floor (see strengths) shows what the alternative guarantee
looks like.

### 2. Fees are invisible exactly where they decide the sign of the edge

- Live `pnl_percent` does not subtract the Kraken fee (~0.16–0.26 % per order).
- The optimizer accepts `fee_pct` but **defaults to 0.0** — with zero fees the
  search rewards hyperactive configurations whose edge evaporates at real fee
  tiers.
- The moves being captured are of order 1–2×ATR (≈ 0.2–0.5 % on 15-min
  candles), i.e. the same order of magnitude as a sell→buy-back cycle's fees
  (~0.3–0.5 %).

Nuance worth keeping: *versus rebalancing immediately*, the fee cancels (either
way it is one order), so timing alpha is fee-neutral against that baseline. But
*versus not trading at all*, cycle frequency — which K_ACT/K_STOP control — is a
pure fee cost, and that is exactly the comparison the optimizer's cumulative
PnL makes. Running it with `fee_pct = 0` optimizes a fee-free world.

### 3. Backtest↔live divergence is systematically optimistic

The engine (`trading/engine.py::simulate_operations`) executes exactly at the
stop price, uses intra-candle high/low for trailing, pays no spread/slippage,
and simulates an always-in-market alternating cycle with full reinvestment. The
live bot samples prices every `SLEEPING_INTERVAL` (60 s, missing intra-tick
extremes), places a limit at the *current market* price only after the stop is
already crossed (worse than the stop), and sizes positions from allocation
drift (sometimes no trade is possible). Every one of these gaps inflates
simulated PnL relative to live. Cheap mitigations: always pass a realistic
`fee_pct` (+ a slippage term) to backtests/optimizations, and periodically run
CURRENT mode against the observed live results to measure the gap empirically.

### 4. Volatility classification uses absolute ATR over full history — it conflates price level with volatility

Percentile boundaries (P20–P95) are computed on the ATR **in EUR** across the
entire stored history (`parameters_manager.calculate_trading_parameters`). If
the price doubles (BTC 30k → 68k), identical *relative* volatility produces
double the absolute ATR, so the present is permanently classified HV/HH and the
LL/LV buckets are populated only by old, cheap history — K_STOP selection ends
up driven by the price trajectory, not the current regime. The K values
themselves are ratios (move/ATR) and are immune; only the *level
classification* is biased. Fix concept: classify on **relative ATR
(ATR/close)**. The planned Auto-Lookback window mitigates but does not remove
the unit problem.

### 5. Without a regime filter, chop is the expected bleed mode

In a sideways market whose swings are comparable to activation+stop distance,
the bot repeatedly activates and stops out, capturing ≈ 0 and paying a fee per
cycle. Already recognised: the backlog's Trend/Chop Regime Filter card is the
right answer, and its two-stage rollout (observe first, gate entries second) is
the right order.

### 6. Re-anchoring prioritises "execute the rebalance" over "protect the price" — undocumented

After a strong adverse move, a SELL plan re-anchors its activation down toward
the current price and sells into the first bounce, recording a large negative
`pnl_percent` vs the original entry reference. This is a coherent choice for a
rebalancer (executing late beats never executing), and it is the main source of
the worst recorded per-trade numbers — but the trade-off is stated nowhere.

### 7. Kraken's `ordermin` is never checked

`build_pairs_map` does not capture `ordermin` from `AssetPairs`, and
`MIN_VALUE=10€` does not guarantee it per pair (minimums vary by asset). A
closing order below the minimum would be rejected by Kraken on every tick.
Cheap to close: capture `ordermin` into `config.PAIRS` and enforce it in
`calculate_position` / `close_position`.

### 8. Minor

- `calculate_k_stops` ceils K to the next 0.1 (`math.ceil(k*10)/10`) — a
  systematic widening bias that is coarse for small K values.
- Pivot-based calibration is ex-post by construction (pivots are only known in
  hindsight). Acceptable as a *noise estimator*; must never be read as a signal.
- CLAUDE.md still mentions regime "ER thresholds" resolved inside
  `simulate_operations`; no such logic exists in the engine (doc drift).

## Recommendations (prioritized)

Items 1, 2 and 5 are measurement/tooling — they change no trading behaviour and
need no strategy discussion. Items 3 and 4 **are** strategy changes and require
an explicit decision before implementation (per the project's invariants).

1. **Measure real economic PnL.** Record the actual fee of each fill
   (`QueryOrders` returns it) alongside `pnl_percent`, and add a live benchmark
   "portfolio value vs holding the target allocation" (Grafana row). Until this
   exists, whether the timing layer adds value is unknowable.
2. **Make fees non-optional in the optimizer/backtest** — default `fee_pct` to
   the real Kraken tier instead of 0.0, and add a slippage term to the engine's
   execution price.
3. **Classify volatility by relative ATR (ATR/close).** Small change in
   `parameters_manager` + engine calibration, large methodological correction.
   *(Strategy change — discuss first.)*
4. **Analyse and bound the K_ACT↔K_STOP relationship** (the per-trade loss
   floor), and document the MIN_MARGIN guarantee, the real semantics of
   `pnl_percent`, and the re-anchoring trade-off in `trading-strategy.md`.
   *(The documentation part is free; any constraint on the relationship is a
   strategy change.)*
5. **Capture and enforce `ordermin`.**
6. **Ship the already-planned regime filter**, observation stage first.
