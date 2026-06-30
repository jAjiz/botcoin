# Optimizer Search Grids — Design

Status: **Grid design decided** (fixed reasoned grids; no per-pair derivation engine).
**All PnL-based validation and config selection is deferred** — at ~49d the optimizer
overfits and out-of-sample PnL collapses (see "Walk-forward"). Decisions here rest on
structural/mechanical evidence, not absolute PnL. The open question — *is a single robust
config even attainable, or only regime-specific ones?* — needs more data (see "Open question"
and "How to reach a final conclusion").

## Outcome

The optimizer's `SearchSpace` (`stop_pcts`, `k_act`, `min_margin`) has no defaults — an
operator hand-picks `start/end/step`. This work set sensible **fixed** grids, informed by
one measurement pass (XBTEUR + ETHEUR, ~49d, 15m) and a multi-seed validation. Both
branches share `stop_pcts`:

| Dimension | GridSpec (start, end, step) | values | rationale |
|-----------|------------------------------|--------|-----------|
| `stop_pcts` (per level) | (0.5, 0.9, 0.1) | 5 / level → 3 125 combos | floor 0.5 = "survives ≥ half the structural noise"; step 0.1 matches K_STOP's 0.1 quantization |
| `k_act` | (0.0, 6.0, 0.5) | 13 | leg/ATR p50 ≈ 9–10; validation winners interior at 4.5; over-wide upper self-penalizes |
| `min_margin` | (0.0, 0.010, 0.002) | 6 | ATR/price ≈ 0.003; additive==structural at ≈ 0.008–0.010 |

- `MINIMUM_CHANGE_PCT` (pivot noise filter, a **live-bot** parameter) stays **0.020**.
  Lowering it is structurally worse (fewer samples + tighter K_STOP); the supporting PnL
  comparison is in-sample-optimistic, so "keep 0.020" rests on the structural argument.
- For pairs whose `ATR/price` differs materially from XBT/ETH (low-value pairs), scale the
  `min_margin` band by the pair's `median(ATR/price)`; the rest are pair-agnostic.
- The provisional bounds (`stop_pcts` floor, `k_act` upper, `min_margin` ceiling) are
  revised by the **edge-pinning** check once a pair has enough trades for PnL to be trusted
  — which, per the walk-forward, is **more than ~49d**.

## Why fixed grids, not a per-pair derivation engine

The effort began as "derive an optimal `SearchSpace` per pair" and collapsed to fixed
grids because the hard parts turned out not to need derivation:

- **`stop_pcts` is a scale-free percentile.** Searching `[0.5, 0.9]` is valid for any
  sample size, and one shared band yields a different K_STOP per level automatically
  (each level has its own K distribution) — so the levels stay independent with no
  per-level grids.
- **`k_act`'s upper bound is self-correcting.** A `k_act` so high the position never
  activates produces 0 ops and scores `-1e18` (ranked last), so an over-wide upper only
  wastes trials, never corrupts the result.
- **`min_margin` is the only pair-specific scale**, handled by a one-line `× ATR/price`,
  not a routine.

## Activation math (reference)

The two mutually-exclusive branches (hence the two optimizer studies):

```
k_act branch:      distance = k_act × ATR
min_margin branch: distance = K_STOP × ATR + min_margin × price
K_STOP(level)    = ceil( quantile(K_values_level, stop_pct) × 10 ) / 10
```

`K_values` are the per-event, per-level maxima of `drawdown/ATR` (uptrend → sell side) or
`bounce/ATR` (downtrend → buy side): the structural adverse-excursion distribution.

## Per-dimension rationale

### stop_pcts — (0.5, 0.9, 0.1)

`stop_pct` is "what fraction of observed structural noise the stop tolerates"; the chance
a swing's max-noise exceeds the stop is ≈ `(1 − stop_pct)`.

- **Floor 0.5** (economic + regularizer): at 0.5, K_STOP = the *median* per-swing max-noise
  → ~50% whipsaw; below it the majority of normal noise blows through the stop. It also acts
  as **overfit regularization**: tested widening to `[0, 0.9]` on ETHEUR, the optimizer dove
  to the extreme (stops at 0.0) and reported huge in-sample PnL (robust 30.71), but the
  walk-forward refuted it — `[0, 0.9]` overfit *harder* (out-of-sample worse than `[0.5,
  0.9]`, ~−7.7 vs −2.4), and the 100 000-combo space (vs 3 125) worsens convergence. The
  floor keeps the optimizer out of the degenerate ultra-tight-stop region. **Do not lower
  it.** (Note: `K` is already ATR-normalized, so a more volatile pair does **not** need a
  lower percentile — ATR does the scaling.)
- **Ceiling 0.9**: p95/max is a single unstable sample at low n.
- **Step 0.1**: K_STOP is quantized to 0.1 ATR, so finer steps re-test identical stops
  (XBTEUR HV: {0.5…0.9} → K_STOP {3.0,3.2,3.4,3.8,4.2}, 5 distinct). With 5 independent
  levels the step drives the `n⁵` combinatorics, so 0.1 is the balance; drop to 0.2 (243
  combos) only if AUTO can't converge. (`[0.5,0.95]` isn't divisible by 0.1 → band is
  `[0.5,0.9]`.)
- `n = 0` levels are left to the existing `get_k_stop`/`lookup_k_stop` fallback, not the grid.

### k_act — (0.0, 6.0, 0.5)

`distance = k_act × ATR`; a single per-pair value. Anchored by the favorable-leg/ATR
distribution (move available from a pivot to the next, in ATR units): p50 ≈ 9–10 across
XBT/ETH, validation winners interior at 4.5. Lower bound 0 (immediate activation is valid);
step ≥ 0.5 (0.1 increments are practically identical). The upper is self-correcting, so
precision there doesn't matter.

### min_margin — (0.0, 0.010, 0.002)

Only in the non-`k_act` branch: an additive margin on top of `K_STOP × ATR`. Its scale is
`ATR/price` (≈ 0.003 for XBT/ETH); the additive term matches the structural term at
`min_margin ≈ K_STOP × ATR/price ≈ 0.008–0.010`, which sets the ceiling. Lower bound 0.
For off-scale pairs, scale the band by the pair's `median(ATR/price)`.

## What we deliberately did NOT add (and why)

- **A per-pair derivation routine / endpoint** — unnecessary (see "Why fixed grids").
- **`MIN_LEVEL_SAMPLES` / CDF-derived per-level bounds** — the live bot already handles the
  only hard case (`n = 0`) via `get_k_stop` (opposite side → neighbors), and the engine
  mirrors it (`lookup_k_stop`). For `n ≥ 1` live uses the raw quantile with no gate, so a
  derivation-side gate would tune a parameterization production never uses → divergence.
- **Shrinkage for thin tail levels** — would change the K_STOP the live bot trades: a
  separate live-bot strategy change, validated on its own, not folded in here.
- **Coupling the 5 stop levels** — defeats volatility classification (levels behave
  differently by design).
- **Optimizing K_STOP directly** instead of via percentiles — loses the "percentile of
  observed structural noise" semantics.
- **Lowering `MINIMUM_CHANGE_PCT`** — validated against, see Evidence.
- **A train/test bound-derivation discipline** — moot once bounds are fixed, not derived.

## Guardrails

- Grids are reasoned from **structural** distributions (K-values, ATR percentiles,
  ATR/price) — never from PnL or the test split. PnL only enters the *validation* of a grid
  (edge-pinning), never its selection.
- The optimizer must mirror live behavior (the engine reproduces `get_k_stop`); a grid that
  tunes a parameterization production doesn't use is rejected.

## Evidence (measurements)

`scripts/analysis/grid_derivation_explore.py` (read-only; `--sweep` for MINIMUM_CHANGE_PCT).

**Sample scarcity.** Per level/side over ~49d: LL≈6, LV≈10–20, MV≈15–32, HV≈19–27, HH≈4–8.
The tails are starved **by design** — levels are ATR-percentile bins (LL=<p20, HH=>p95), so
HH is ~5% of candles by construction and stays smallest no matter the history length. This
is why per-level fitted grids were rejected in favor of a fixed scale-free band. **Scarcity
is not the binding constraint, though** — see "Open question" below: few samples per level is
the *nature of the market* (K_STOP is descriptive of what the market did), and the real
limiter is non-stationarity, which more samples do not fix.

**`MINIMUM_CHANGE_PCT` sweep → keep 0.020 (structural grounds).** Lowering it adds LL/LV
samples (XBTEUR LL 6→17 at 0.010) but pulls median K down (LL 6.8→3.8) → tighter K_STOP. HH
is immune (structural). A PnL comparison of 0.015 vs 0.020 (OPTIMIZE, 4 seeds) had XBTEUR
favor 0.020 in-sample, but the walk-forward later showed all such in-sample PnL collapses
out-of-sample — so the decision rests on the **structural** trade-off (lowering = fewer
samples + tighter, more-whipsaw K_STOP), not on the PnL. *Raising* mcp (0.025/0.030) does
widen stops (median K up: XBTEUR HV 3.0→6.0 at 0.030) but cuts pivots ~40% (49→29) and thins
the *mid* levels too (XBTEUR MV 15→8, HV 19→11), destabilizing calibration; HH is immune
either way. So raising it trades wider stops for scarcer/noisier calibration — `0.025` is the
gentler step, but it's a live-bot change that can't be judged by PnL at 49d, so **not now**.
Lesson: sample density is not a substitute for out-of-sample validation.

## Lookback window (strategy note)

Crypto is non-stationary, so the data window is a design parameter. **Recent data (≤ ~6
months) for config selection; older history for robustness validation** (walk-forward) —
not "stale and useless". The codebase already splits this: structural calibration runs over
all history; config selection wants recency. The window length is itself a hyperparameter to
validate, not a fixed number. With only ~49d here, PnL over 7–22 trades is noisy — hence
grids are chosen on structural grounds, with PnL validation interpreted cautiously.

## Validation plan (before adopting as defaults)

Tests on the optimizer, by reliability at the current data volume.

**Reliable now (mechanical / structural):**

1. **Edge-pinning** — per pair, several seeds: does the winning candidate sit on any grid
   boundary? Pinned → widen that bound. (Use seeds, not one run — single-seed is noisy.)
2. **Candidate coverage** — fraction of trials that produce ≥ `min_ops` trades. Mostly
   degenerate → the band is mis-scaled (esp. `min_margin` for off-scale pairs).
3. **AUTO convergence** — does AUTO agree on a config within budget? It validates the step
   (3 125 combos): if it can't converge, coarsen `stop_pcts` to 0.2.
4. **Coarseness sensitivity** — compare `stop_pcts` step 0.1 vs 0.2 vs 0.05 on best
   robust_pnl and convergence; confirm 0.1 is the right balance.

**Run but interpret cautiously (PnL-based, weak at ~49d; firm conclusions deferred to ≥60–90d):**

5. **Train/test gap** of the winners — a grid that consistently finds large-divergence
   configs is enabling overfitting.
6. **Walk-forward** over a few sub-windows — is the best region stable or does it jump?
7. **CURRENT vs OPTIMIZE** — does the grid's best beat the live config? (sanity).

Common settings: `fee_pct=0.4`, `train_split=0.67`, pairs XBTEUR + ETHEUR, multiple seeds.

### Validation results (tests 1–4 + convergence)

Run with the fixed grids, `fee_pct=0.4`, `split=0.67`, 3 seeds (1/4) / 4 seeds (3), 300
trials (1/2/4) / 1200 max (3). Driver: `scripts/analysis/grid_validation.py`.

- **Test 2 (coverage) — PASS.** 100% of random grid candidates produce ≥1 train and ≥1
  test op on both pairs (median 22/36 train ops, zero degenerate). Bands are well-scaled;
  `k_act=6` still activates (the upper is conservative, which is fine — it self-corrects).
- **Test 1 (edge-pinning) — bounds sound; tails are noise.** Data-rich dims stay interior
  (`MV` never pins; `LV`/`HV`/`k_act`/`min_margin` touch a single edge occasionally). The
  tail levels `LL`/`HH` pin to **both** edges across seeds — the signature of a
  noise-dominated parameter, **not** a bounds problem (widening won't help). Weak genuine
  signals: `min_margin` ceiling (ETHEUR) and `HV` ceiling (XBTEUR) — revisit with more data.
- **Test 4 (coarseness) — confirms step 0.1.** XBTEUR robust by step: 0.1 → {4.16, 4.03,
  3.77} (tight, all min_margin branch); 0.2 → {0.11, 2.56, 1.35} (too coarse, scattered,
  worse); 0.05 → {1.42, 5.18, 3.57} (overfit, even switches winning branch). 0.1 is the
  balance.
- **Test 3 (AUTO) — exact-match criterion never converges (0/4 both pairs).** Diagnosis
  from the per-seed winners: the failure is **not** disagreement — seeds agree per
  dimension on the core (XBTEUR `min_margin=0.004` and `MV=0.8` unanimous; ETHEUR `LV=0.6`
  unanimous) but the whole-tuple match needs all 5+1 dims to align in one seed, which the
  2/3 per-dim splits never do. Dropping `LL`/`HH` alone is insufficient (the rich levels
  still split across different seeds).

**Convergence criterion (validated, gated majority).** Per-dimension **majority vote**
assembles a config; that config is **evaluated** and convergence is gated on it matching
the best individual seed:

- XBTEUR assembled robust **4.18** ≈ best seed 4.16 → converge (deploy assembled).
- ETHEUR assembled robust **−2.77** < best seed −1.36 → **reject** (dims interact under
  noise; majority assembly is worse than any seed — correctly not converged).

So blind per-dim majority is unsafe (can deploy a worse-than-seed config on a noisy pair),
but **majority-assemble → evaluate → converge only if assembled ≥ best-seed (− tolerance)**
converges on signal (XBTEUR), fails safe on noise (ETHEUR), and never returns worse than
the best seed. This is the path to implement (a validated optimizer change), replacing the
exact whole-tuple match. (`MIN_LEVEL_SAMPLES`-style gating is still not needed.) The
*mechanism* is sound on structural grounds; the specific pass/fail numbers above are
in-sample and provisional (see Walk-forward).

## Walk-forward (out-of-sample) — the decisive caveat

The tests above split train/test *within* one ~49d window. That is **not** a real
out-of-sample test — both halves are the same regime. A true walk-forward (optimize on the
first ~24d `W1`, evaluate the winner on the disjoint second ~24d `W2`) shows the in-sample
numbers are badly optimistic:

| | W1 (in-sample) | W2 (out-of-sample) |
|---|---|---|
| ETHEUR `[0.5,0.9]` | ~9 | −0.3 / −5.7 / −1.2 |
| ETHEUR `[0,0.9]` | ~13–17 | −6.7 / −11.0 / −5.4 |
| XBTEUR `[0.5,0.9]` | ~18–21 | −16.1 / −11.9 / **+4.7** |
| XBTEUR `[0,0.9]` | ~19–21 | −7.8 / −8.5 / −7.6 |

**Both pairs collapse out-of-sample** (5–6 of 6 configs negative on W2, from +18–21%
in-sample). Optimizing on ~24d (15–25 trades) is severe overfitting; the full 49d is little
better. Consequences:

- **No optimized config is deployable yet** — none generalize.
- **Every absolute-PnL conclusion in this doc is provisional** (the mcp PnL comparison, the
  "profitable" XBTEUR runs, the convergence pass/fail). Their *structural / relative* logic
  stands (coverage 100%, the LL/HH-are-noise edge-pinning pattern, step-0.1 seed-consistency,
  the gated-majority mechanism); the *numbers* do not.
- **Firm PnL-based validation and config selection must wait for substantially more data**
  (≥60–90d, and judged by walk-forward, not the within-window split). Until then, lean on the
  mechanical/structural evidence.

## Open question: is a robust config even attainable?

This is the real question, and it is **not yet answerable** with ~49d. The careful framing:

- **"Scarcity" is the wrong way to see it.** Few K-samples per level is the *nature of the
  market*, not a measurement defect — `K_STOP` is *descriptive* of what the market actually
  did at each volatility level. Chasing more samples by lowering `MINIMUM_CHANGE_PCT` does
  **not** give more samples of the same noise; it *redefines* what counts as noise (includes
  smaller swings) and pollutes the estimate — which is why it tested worse.
- **Two separable problems, often conflated:** (1) within-regime *estimation noise* (few
  samples → noisy percentile — minor, and the live `get_k_stop` fallback already covers the
  `n=0` extreme); (2) cross-regime *non-stationarity* (the optimal config in one period isn't
  optimal in another). The walk-forward collapse is the signature of **(2)**, not (1) — and
  more samples fix (1), never (2).
- **For rare levels the two are entangled:** getting more samples for LL/HH *requires* more
  history, which *necessarily mixes more regimes*. You cannot reduce estimation noise for a
  rare level without averaging across regimes — so "more data" and "more regime mixing" come
  together, and chasing sample count for the tails is self-defeating if the market is
  non-stationary.

**So is a single time-invariant robust config attainable, or only regime-specific configs?**
Unknown at 49d. The walk-forward collapse is **consistent with both**: genuine
non-stationarity (robustness unattainable) *and* mere undersampling (with 15–25 trades per
window, even a stationary process collapses W1→W2 from PnL estimation noise alone). The two
cannot be distinguished here.

## How to reach a final conclusion (when more data exists)

The goal is **temporal breadth — more trades across genuinely different regimes — not more
pivot density.** Do **not** revisit `MINIMUM_CHANGE_PCT` or chase samples; that addresses
neither problem. Instead:

1. **Accumulate history** until there are ≥3 disjoint windows of ~45–60d each, ideally
   spanning at least one trending and one choppy regime (~140–180d total).
2. **Re-run the walk-forward** per pair: optimize on each window with the fixed grids, evaluate
   the winner on the *next* disjoint window. Use the existing harness
   (`scripts/analysis/grid_validation.py walkforward <pairs> --windows 3`), `fee_pct=0.4`,
   several seeds.
3. **Decide:**
   - If a config (or the gated-majority-assembled config) **generalizes across windows** →
     a robust config exists. Finalize the grids, ship the **gated-majority AUTO criterion**
     (already designed and structurally validated here), and select a deployable config.
   - If each window wants a **different** config → robustness via a single static config is
     unattainable. The architectural answer is **regime-aware configuration**: detect the
     regime and switch config — i.e. the **Trend/Chop Regime Filter** already in
     `docs/BACKLOG.md`. The optimizer would then tune *per regime*, not globally.

Everything structural in this doc (the fixed grids, the rejected machinery, the convergence
mechanism) stands regardless; only the **PnL-based final selection** waits for this.
