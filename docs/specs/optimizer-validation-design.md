# Optimizer Validation — Design and Study State

Status: **two measurement defects fixed and shipped; three search defects open.** The
question this document exists to answer is unchanged — *can the optimizer produce a
config that beats buy-and-hold out of sample?* — but the answer it carried before
2026-09-02 rested on measurements that were wrong. Everything PnL-based in the previous
version of this file (`optimizer-grid-derivation-design.md`) is retracted; see
"Retracted".

This file supersedes that one. It is the handoff: read "Where the study stands" and
"How to continue" first.

## The question

The bot must beat holding the asset. Not "be profitable" — in a rising market anything
long is profitable, and in a falling one avoiding the market looks like skill. The only
figure that means anything is the **euro value of the portfolio against buy-and-hold over
the same window**, measured out of sample.

Everything below is XBTEUR, 15-minute candles, **0.4 % fee per leg** (the bot's limit
orders usually fill as maker; the 0.8 % taker figure used earlier overstated the cost by
2×). The window with continuous data is **2025-01-01 .. 2026-03-31** (43 610 simulated
bars; 54 297 in `ohlc_data` counting pre-2025 history used for calibration). It is bearish
end to end: **buy-and-hold returns −35.1 %** over the full window.

## Where the study stands

**Established.** With the two fixes below applied, in a single continuous run over the
full window, a band of conservative configs beats buy-and-hold by a wide margin, and the
optimizer finds one when the search space can express it:

| Search space | Best in-sample candidate | Euro result | vs hold | ops | seed agreement |
|---|---|---|---|---|---|
| `min_margin` ≤ 0.010 (the old grid) | `mm=0.006 s=0.9` | **−58.6 %** | −23.6 | 91 | 3/3 |
| `min_margin` ≤ 0.10 | `mm=0.035 s=0.9` | **+34.3 %** | +69.3 | 29 | 3/3 |

**Not established.** No out-of-sample result is trustworthy yet. The honest test — fit on
the first N days only, then run continuously over the rest — was measured *before* the
objective was corrected, so its **scoring is valid but its config selection is not**. It
must be re-run (task 1 under "How to continue"). For the record, what it produced under
the broken objective:

| Fit window | Config chosen | ops | Euro result | Hold | vs hold |
|---|---|---|---|---|---|
| 60 d | `mm=0.090` (3/3 seeds) | 3 | −21.0 .. −21.9 % | −29.3 % | **+7.3 .. +8.3** |
| 120 d | `k_act=9` / `k_act=11` | 37–59 | −24.1 .. −44.8 % | −30.2 % | −14.6 .. +6.1 |
| 180 d | `k_act=9` / `k_act=11` | 21–37 | −28.1 .. −46.8 % | −35.9 % | −10.8 .. +7.7 |
| 240 d | `k_act=11` / `mm=0.090` | 3–17 | −27.5 .. −31.1 % | −37.6 % | +6.5 .. +10.1 |

Two things to read from it. Longer fit windows are **not** better: 120 d and 180 d push
the optimizer into the `k_act` branch, which trades 3–10× more and loses to hold on two
seeds of three. And every honest pick sits near the top of the widened `min_margin` grid
(0.090 of 0.10) — the search runs away from the profitable band (0.030–0.050) toward
"trade as little as possible". Whether the corrected objective still does that is exactly
what task 1 measures.

## The six wrong assumptions

Ordered by size of effect. The first two are fixed and shipped; the rest are open.

### 1. A cash leg was paid as if the bot held a short — FIXED (`3ff283b`)

The bot is spot. Between a sell and the next buy it holds euros, and a euro balance does
not move with the price. Both `trading/engine.py` and `positions_manager.finalize_close`
booked `entry − closing_price` for that leg, which is the payoff of a short position.

The gain from buying back cheaper is real, but it is **already counted**: the next long
leg measures its move from the lower entry. Booking the cash leg on top counts it twice.
The error is the *product* of every cash leg, so it cancels when the legs are short and
alternate, and compounds when they are long and all point the same way — which is why it
stayed hidden and why it hits hardest exactly the low-frequency configs the optimizer
prefers:

| Config | ops | Old engine metric | Euro portfolio | Overstated by |
|---|---|---|---|---|
| `mm=0.035 s=0.9` | 29 | +196.2 % | +34.3 % | **+162.0** |
| `mm=0.040 s=0.8` | 20 | +110.8 % | +15.1 % | +95.7 |
| `mm=0.030 s=0.9` | 22 | +59.0 % | −1.0 % | +60.0 |
| `mm=0.010 s=0.6` | 142 | −60.5 % | −59.9 % | −0.5 |
| `mm=0.500 s=0.9` | 1 | −35.1 % | −35.1 % | 0.0 |

`_pnl_abs` now returns zero for a cash leg, which also fixes `mark_to_market` (a run
ending in euros has nothing to value), and `_leg_pct` charges that leg its fee on the
euros it spends. The engine's total now matches an independently written portfolio
reconstruction to 0.0 on every config tested.

**Production changed too, and the historical break was accepted.** `pnl_percent` for a
closed BUY position is now the euro result (the fee alone), not the price move. Rows
written before `3ff283b` are not comparable with rows after it. The log line still reports
how much cheaper the buy-back was.

### 2. Neither the optimizer nor the backtest recalibrated — FIXED (`4b5e736`)

Production recalibrates every `PARAM_SESSIONS` ticks over all history up to that moment.
`EngineConfig.calibration_schedule` could model that, but no in-tree caller built one:
each ran a whole window on a **single** calibration derived from history up to the
window's *end*, so every bar before that end was scored with `K_STOP` values and level
thresholds computed from its own future.

`market_analyzer.build_calibration_inputs` now walks the window at
`core.config.RECALIBRATION_BARS` (48 bars = 12 h with the current config) and calibrates
each point from the past only. The points are candidate-independent, so the optimizer
builds them once per run and shares them across every seed and trial.

This **reorders** candidates rather than shifting them all:

| Config | Single calibration | With schedule | Δ |
|---|---|---|---|
| `mm=0.035 s=0.9` | −5.2 % | +32.4 % | +37.6 |
| `mm=0.030 s=0.9` | −28.2 % | −1.0 % | +27.2 |
| `k_act=11.0 s=0.8` | +12.3 % | +5.5 % | **−6.9** |

The `k_act` branch moves the *other way*. A single calibration systematically flattered
`k_act` and punished `min_margin` — the branch the profitable band lives in.

Two consequences to carry forward:

- **Cost.** About 380 s for a 15-month window (907 points), paid once per run, and the
  process prints nothing while it runs — a job or a `/backtest` that looks hung for six
  minutes is doing this. An async optimizer job absorbs it; a synchronous `/backtest` on a
  long window does not, hence `recalibration_bars` on both requests (`null` = live cadence,
  `0` = one calibration). Any harness that re-fits the same window per arm and per seed
  must memoize the schedule, or it pays that cost once per fit — see Tools.
- **Coarsening is not a safe approximation.** At 192 bars instead of 48, `mm=0.035 s=0.9`
  moves from +34.3 % to −1.4 %; at 480 and 960 bars it returns to +35.4 % and +34.4 %. The
  outcome hinges on whether a recalibration lands before or after a particular bar. That
  is a real property of the strategy, and it caps how much confidence any single number
  here deserves.

### 3. The `min_margin` ceiling made buy-and-hold unreachable — OPEN

This was the binding constraint on the whole study, and the reason every earlier
conclusion pointed the wrong way.

`min_margin` is the **only** parameter that puts an activation floor that does not scale
with ATR. The old grid capped it at **0.010**. Measured consequences over the full window:

- The most quiescent config the old grid can express still trades **69 times** in 15
  months. Buy-and-hold (1 operation) is **not** reachable.
- With perfect hindsight, **0 of 95** configs in the old grid beat buy-and-hold. The best
  in-sample candidate lands 24.5 points **below** hold — which can only happen when the
  quiescent config is outside the space.
- Widening `min_margin` to 0.20 and re-sweeping: **37 of 60** configs beat hold.

Why 0.010 is small: the activation barrier in that branch is `K_STOP × ATR + min_margin ×
price`. `K_STOP` is calibrated per level and rises as ATR falls, so the `K_STOP × ATR`
term sits at a near-constant **1.8 %–2.9 %** of price at every level. Adding 1 % gives a
3–4 % barrier, which BTC crosses about once every 6.5 days on 15-minute candles. The
profitable band needs 7–8 %.

**Action:** raise the `min_margin` grid ceiling to at least 0.10.

### 4. `stop_pct = 1.0` is a sample maximum, not a percentile — OPEN

`calculate_noise_between_pivots` stores **one K value per trend leg per level**: the
deepest retracement of that leg. `stop_pct` is a percentile over that sample, and the
samples are small — 53 to 186 per level over 15 months:

| Level | Samples (sell) | K at 0.9 | K at 1.0 | Widening |
|---|---|---|---|---|
| LL | 53 | 16.1 | 24.0 | +49 % |
| LV | 124 | 8.3 | 12.1 | +46 % |
| MV | 177 | 6.5 | 10.1 | +55 % |
| HV | 153 | 4.3 | 6.7 | +56 % |
| HH | 58 | 3.8 | 6.5 | +71 % |
| HV (buy side) | 154 | 4.7 | 9.1 | **+94 %** |

At `stop_pct = 1.0` the stop is set by a **single observation** out of 53–186, and a
sample maximum grows with the length of history — so what `1.0` means drifts as data
accumulates. This produced a 35-point swing between `s=0.9` and `s=1.0` in the sweep and
made a two-cell region look like a profitable plateau when it was an artefact.

**Action:** cap the `stop_pcts` grid at 0.9. The old floor of 0.5 is untouched by this
finding and the argument for it still stands.

### 5. The two activation branches are structurally disjoint — OPEN

`trading/engine.py`:

```
if k_act is not None:
    return k_act * atr_val                              # min_margin ignored entirely
return k_stop * atr_val + (min_margin * reference_price)
```

They are not two settings of one space. The `k_act` branch has **no** ATR-independent
floor, so its barrier collapses exactly when the market is calm:

| ATR/price quantile | Barrier at `k_act=6` | Barrier at `min_margin=0.010` |
|---|---|---|
| 0.05 | 0.53 % | 1.00 % |
| 0.25 | 0.99 % | 1.00 % |
| 0.50 | 1.46 % | 1.00 % |
| 0.95 | 3.64 % | 1.00 % |

(ATR/close on 15-minute XBTEUR: median 0.244 %, p90 0.489 %.) This is why `k_act=6.0`
trades 193 times where `mm=0.010` trades 69, despite the higher nominal ceiling, and why
the honest fits that landed in the `k_act` branch lost to hold.

No candidate can express "a moderate ATR multiple plus a fixed floor". The entire
profitable region lives in the `min_margin` branch because it is the only one with a
floor.

**Action:** make the space two-dimensional so `k_act` and `min_margin` coexist. This is a
strategy change, not a refactor — it changes what the live bot can be configured to do,
and `activation_distance` is mirrored in `positions_manager`.

### 6. The objective is absolute PnL, never relative to the benchmark — OPEN, low priority

`robust_pnl = min(train_pnl, test_pnl)` over `mark_to_market`. Buy-and-hold appears
nowhere. In a trending window this selects for exposure rather than skill: in this bearish
window "do almost nothing" scores well, and in a bull window the opposite would.

It matters less than it looks, because `min_ops` defaults to 0 and a config that never
activates *is* buy-and-hold — the engine's first operation is always the opening buy, and
`mm=0.500` scores exactly −35.07 %, the same as hold minus one entry fee. So the
optimizer's in-sample result is **floored by buy-and-hold**, but only once the space can
express quiescence, which is defect 3. Fix 3 first and re-measure before deciding whether
a benchmark-relative objective is still needed.

## Harness defects (measurement, not production)

These bit the study and would bite again.

**Segment-restart walk-forward is invalid for low-frequency configs.** Scoring a
config over 28 disjoint 14-day segments restarts the simulation 28 times: it charges 28
entry fees the running bot never pays (visible as a constant −0.39 gap in every zero-op
segment), and — far worse — it liquidates and re-opens the position 28 times, destroying
the mechanism a trailing stop that rides a trend for weeks depends on. A config trading
once every three weeks cannot be evaluated in two-week slices. It changed both the level
and the **order**: `mm=0.050 s=0.9` scored +10.2 % chained and +33.6 % continuous;
`mm=0.030 s=1.0` scored +24.9 % chained and +15.3 % continuous. **Score a candidate on one
continuous run over the whole forward span.**

**The in-tree schedule anchors to the window, production anchors to its own clock.**
`build_calibration_inputs` places its points at multiples of `recalib_bars` from the
*window's* first bar. The live bot recalibrates on a fixed cadence regardless of where an
analysis window happens to start. For a full-history run the two coincide; for a sliced
job they do not. `scripts/analysis/refit_frequency_experiment.py` anchors to the frame,
which is the faithful choice. **Open: decide whether to move the in-tree builder to a
frame-anchored grid.** Given defect 2's sensitivity measurement, this is not cosmetic.

**The harnesses collapse the five per-level `stop_pcts` into one shared value.** The real
`SearchSpace` searches five independently: 6⁵ = 7 776 combinations per branch, and
`_quantile_ceiled` rounds K to 0.1 so many are identical — a coarse step landscape with
large flat plateaus, poor terrain for TPE. Results measured with one shared stop
**understate the search difficulty the deployed optimizer faces.**

## Retracted from the previous version of this document

- **The whole walk-forward table** (`W1`/`W2`, both pairs collapsing out of sample). It was
  measured with defects 1 and 2 present and inside the old grid. The numbers do not stand,
  and neither does the inference drawn from them.
- **"Edge-pinning — bounds sound."** It flagged the `min_margin` ceiling as a "weak genuine
  signal, revisit with more data". That ceiling was the binding constraint on the entire
  study. A boundary that a candidate touches deserves widening and re-measuring, not a note.
- **"No optimized config is deployable yet — none generalize."** Premature. The search
  space could not express the profitable region, so the collapse was not evidence about
  the market.
- **"Is a single robust config attainable, or only regime-specific ones?"** Still open, but
  the framing changes: the earlier collapse is no longer evidence for non-stationarity.
- **The `k_act` upper bound is self-correcting.** It rests on "a config that never
  activates produces 0 ops and scores −1e18". It does not: the engine always emits the
  opening buy, so such a config scores as buy-and-hold, which in a falling market is a
  *good* score. That is a feature (defect 6), but the stated reasoning was wrong.

**Still standing:** the structural argument for `MINIMUM_CHANGE_PCT = 0.020` (lowering it
redefines what counts as noise rather than sampling more of it); the `stop_pcts` floor of
0.5; that per-level fitted grids are unnecessary because `stop_pct` is scale-free; that
`n = 0` levels are the live `get_k_stop` fallback's job, not the grid's.

## How to continue

In order. Tasks 1 and 2 are cheap and should come first.

1. **Re-run the honest out-of-sample test with the corrected objective.** Fit on the first
   N days only, then score the winner on **one continuous run** over the remainder, in
   euros. Vary N over 60/120/180/240 d and at least 3 seeds. This is the number the whole
   study is for, and the table above is the pre-fix version of it.
2. **Widen the grids** — `min_margin` ceiling to 0.10, `stop_pcts` ceiling down to 0.9 —
   and repeat task 1. Defects 3 and 4.
3. **Unify the activation branches** into one two-dimensional space (defect 5). Strategy
   change: it touches `activation_distance` in both `trading/engine.py` and
   `trading/positions_manager.py`, and needs its own validation.
4. **Decide the schedule anchoring** (harness defects). Frame-anchored is faithful;
   window-anchored is what ships.
5. **Add a benchmark-relative objective** as a request option, if task 1 still shows the
   search running to the quiescent corner (defect 6).
6. **Import 2023–2024 history** with `scripts/import_kraken_ohlcvt.py` and repeat in a
   bull window. Every measurement here comes from one 15-month bear market. A bot that
   leaves the market must lose to hold in a sustained rally, and the size of that loss is
   unmeasured.

Do **not** revisit `MINIMUM_CHANGE_PCT` or chase pivot density; neither addresses any
defect above.

## Tools

All read-only, all require `PYTHONPATH=.` and DB env vars.

| Script | What it answers |
|---|---|
| `scripts/import_kraken_ohlcvt.py` | Loads Kraken's CSV archives into `ohlc_data` (REST only returns ~720 candles). |
| `scripts/analysis/refit_frequency_experiment.py` | Shared harness — bounded frames, memoised OHLC, one shared `stop_pct`, frame-anchored calibration point cache. Answers whether re-fitting often beats fitting once. |
| `scripts/analysis/objective_experiment.py` | 2×2: inner train/test split vs the fit window's own PnL, five free `stop_pcts` vs one shared. |
| `scripts/analysis/regime_filter_screen.py` | Choppiness Index as a whole-window overlay, for the Trend/Chop backlog card. |
| `scripts/analysis/grid_derivation_explore.py` | Reports the structural distributions behind each grid (K per level, leg/ATR, ATR/price). |
| `scripts/analysis/grid_validation.py` | Edge-pinning, coverage, AUTO convergence. **Its `walkforward` mode uses the segment-restart method — see Harness defects; do not trust its chained figures.** |

Both point caches exist because the in-tree `build_calibration_inputs` recomputes on every
call, and a schedule over a long window costs minutes. `refit_frequency_experiment.py`
computes the pair's points once and slices them per window (frame-anchored);
`regime_filter_screen.py` shares that one. `objective_experiment.py` memoizes per window
instead, since every arm and every seed of a transition re-fits the same window —
measured at 76 s for the first arm and 3 s for the next three. Both are installed by
monkeypatching `optimizer.build_calibration_inputs`. **A new harness needs one of them.**

## Activation math (reference)

```
k_act branch:      distance = k_act × ATR
min_margin branch: distance = K_STOP × ATR + min_margin × reference_price
K_STOP(level)    = ceil( quantile(K_values_level, stop_pct) × 10 ) / 10
```

`K_values` are the per-leg, per-level maxima of `drawdown/ATR` (uptrend → sell side) or
`bounce/ATR` (downtrend → buy side).

`reference_price` is **not** the entry price after re-anchoring. `activation_distance` is
re-evaluated against the *current* price whenever the gap exceeds the expected distance
(`trading/engine.py`, mirroring `positions_manager.reanchor_activation_price`), so a sell
position's target follows the price down and `min_margin` is a **barrier width, not a
profit floor**. `PAIR_MIN_MARGIN` is documented in CLAUDE.md as a margin "from entry";
that description is accurate only until the first re-anchor.

## Guardrails

- The only figure that decides anything is the **euro portfolio value against
  buy-and-hold**, out of sample, on one continuous run.
- The optimizer must simulate the bot that is deployed. Two of the six defects above were
  divergences between the simulator and production, and both reordered candidates.
- A search bound that a winning candidate touches is a bound to widen and re-measure, not
  a result to record.
- Before trusting any number, ask what it would look like if the harness were wrong. Three
  of the defects here were invisible until a second, independently written measurement
  disagreed with the first.
