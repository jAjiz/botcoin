# Optimizer Validation — Study State

**Status (2026-09-11): closed as a line of work.** Twenty-four avenues were measured and none
survived. The question this document exists to answer — *can a configuration of the
trailing-stop strategy beat holding the base asset, out of sample?* — is answered **no** for
every parameterisation, gate and objective tested on XBTEUR OHLCV data, including a gate built
with perfect hindsight — and, for the one avenue that needed no prediction at all, on ETHEUR and
SOLEUR as well.

This is a condensed rewrite (2026-09-11) of a 3 100-line running log. What is kept is what a
successor needs: the decisions, the closed avenues with the number that closed each, the
measurement traps that cost this study three false discoveries, and the surviving tools. The
narrative of how each result was reached is in this file's git history.

**The VWAP Z-score model was that next line of work, and it is now measured and closed
(2026-09-11).** It is the twenty-third avenue below. Two things make it worth reading rather
than just counting: it is the first avenue where a causal feature left the null band against the
*honest* label, and it still lost — and the number that closed it is a **break-even fee**, which
is a more useful way to kill a high-frequency rule than a sweep.

**The grid is the twenty-fourth, and it is the only avenue here that never needed a prediction**
(2026-09-11). A grid does not forecast anything; it only needs the price to travel out and back
by more than the toll. That makes it the cleanest possible test of whether the market has
anything at all, and it is also the first avenue run on more than one pair — ETHEUR and SOLEUR
beside XBTEUR, because the toll is proportional and the amplitude is not, so a more volatile pair
is the one structural reason to expect a different answer. It got one, and the answer is still no.
Read it for two things: the gate turns out to select the *wrong variable* for this mechanism, and
on ETHEUR the shuffled control beats the real market outright.

There is no next line of work queued. Read "Decisions taken", "Measurement traps" and "Tools"
before starting one — most of this document's value to that work is negative knowledge and
method, not results.

---

## The question and the objective

The bot must **accumulate more of the base asset than holding it**. Not "be profitable": in a
rising market anything long is profitable, and in a falling one standing aside looks like skill.

The score is `(1 + r_bot)/(1 + r_hold) − 1`, so **holding is 0 % by construction** and is the bar
every figure here is measured against. Euros are a denomination, not the goal — but report both,
because the euro sign has inverted on a run whose base-asset result was positive.

Everything is XBTEUR, **0.40 % fee per leg** (Kraken taker; maker is 0.25 %), on Kraken's OHLCVT
archives — except the grid avenue, which also runs ETHEUR and SOLEUR at the same fee, and where
the fee's other face matters: a round trip pays it **twice**, so no rule whose round trip is
worth less than 0.8 % (0.5 % at maker) can clear it, whatever it predicts. Production fidelity means: **1-minute price path** (`SLEEPING_INTERVAL` = 60 s),
**Wilder ATR over 15-minute bars** projected forward, and the calibration schedule built on the
15-minute frame and remapped by timestamp. Anything measured at 15 minutes is a screen, not a
result.

---

## What was established

### The strategy has no edge, and the reason is mechanical

**Selecting a config by its in-sample result lands at median percentile 50 of the forward
distribution** — chance, over nine decision dates enumerating the whole 105-config space. Config
quality does not persist either: rank correlation between consecutive 60-day periods runs −0.19
to +0.25, median +0.11.

**Whether the bot accumulates is decided by the market period, not by the parameters.** The space
moves in a block: in one 60-day period 0 of 105 configs beat holding, in the next 100 of 105 do,
while inside a period the gap from median to best is about 6 points against a 38-point swing
between periods.

**The conditional edge, separated cleanly 8 of 8.** One config (`mm=0`, `stop=0.9`, capped
re-anchor) over the calendar years 2018-2025 beats holding in exactly the three years holding
lost money and loses in exactly the five it made money. Time in cash is bimodal — 13-22 % in the
winners, 77-99 % in the losers. It is a conditional instrument, not a strategy, and the condition
is the year's direction.

The VWAP Z-score rule lands on the same instrument from a fourth direction, and this is now the
pattern to expect from anything long-or-flat: ungated and at zero fee, **24 of 24** arms lose in
2024 (holding +133.5 % in euros) and **20 of 24** win in 2025 (holding −17.4 %), one arm swinging
−41.4 % → +50.5 %. Any
rule that can sit in cash is a bet on the year's direction until proven otherwise, so **a new
rule must be scored on at least one rising and one falling year, or the result is a coin flip
dressed as a measurement.**

**Over three continuous years it loses 78 % of the base asset in four operations.** 2023-2025
continuous, holding +384 % in euros: production median −76.0 %, **0 of 105 beat holding**.

**The closing arithmetic.** In base asset a run returns roughly
`−drift × time in cash + convexity − fees`. With yearly drifts of +134 % and +149 % the first
term is an order of magnitude larger than the other two, and no configuration, re-anchor variant,
operation frequency or fee level changes it.

### The gate is a real filter and only a filter

Gating — hold the asset by default, trade only when a causal rule opens the gate — was the one
line that reached production fidelity with something to show.

**It works as designed, on three years in two regimes.** `impulso m=0.07 k=7` takes the bot from
−56.3 / −23.3 / +4.7 to **−3.5 / +0.1 / +5.5** across 2023-2025. In 2023 it repairs **52.8 of the
56.3 lost points**. It admits +5.6 % of a +149 % year while staying open 79 % of the time.
Selected over eight years by admitted drift alone, it holds the admitted market inside
[−35 %, +35 %] against a market ranging −62 % to +274 %.

**And it adds no gain whatsoever.** With the gate fixed and only the trailing stop swept:

| gate, selected by | positive in all of 2023-2025 | best worst-year |
|---|---|---|
| flatness — `impulso m=0.07 k=7` | **0/105** | −0.4 % (does not trade) |
| falling drift — `bajo max n=20 p=0.10` | **0/105** | −0.4 % |
| falling drift, wide — `sin max n=10` | **0/105** | −0.4 % |
| VR excess over control — `er n=20 t=0.3` | **0/105** | −0.4 % |
| maximum VR excess — `er n=10 t=0.2` | **0/105** | −0.4 % |

**Five gates, selected by three incompatible objectives, all 0/105.** Every worst-year surface is
monotone in `min_margin` and its optimum is the configuration that does not trade.

**Nothing repeats across windows.** 154 causal detectors at 1-min fidelity on 2023, 2024 and
2025, crossed per variant: **0 of 154 beat holding in all three**, against a chance expectation
of 0.4. The win rate is set by the year — 3 %, 23 %, 42 % — not by the detector, and the winners
are different winners.

**And no gate can fix it, including one built with hindsight.** Hill-climbing 366 free per-day
booleans against the bot's result on 2024 takes it from −29.5 % to +113.1 %. The same hill-climb
on a **shuffled, memoryless** version of the same year reaches +173.5 % and +165.2 %. Four of
four cells, noise admits a *better* oracle gate than the market does. The causal-to-oracle gap is
fitting capacity, not missing signal — so widening the detector search is optimising that
capacity and nothing else.

**And noise beats the market again, on a mechanism with no gate in it at all.** Running the grid
ungated on ETHEUR over 2024-2025, the shuffled control is positive in both years in **5 of 24**
arms — against the real market's **0 of 24** — with a best worst-year of +3.6 % against −15.9 %.
On XBTEUR the same comparison falls short of crossing zero but runs the same way (control −1.0 %,
real −7.7 %). In five of the six pair-by-mode tables the memoryless path is the better market for
a grid. Whatever serial dependence XBTEUR and ETHEUR have at this scale is *persistence*, which is
the opposite of what an oscillation harvester needs. This is an independent reproduction of the
oracle result, by a method that shares no code with it.

**The gate selects the wrong variable for anything that harvests oscillation.** `impulso` admits
calm, and calm is cheap movement: the median per-bar excursion inside the gate is **below** the
one outside it in all six pair-years measured — 0.111 % against 0.139 % on XBTEUR 2024, 0.126 %
against 0.169 % on ETHEUR, 0.198 % against 0.265 % on SOLEUR — about a quarter less amplitude,
while the 1-day efficiency ratio falls by roughly the same proportion (0.084 vs 0.097, 0.085 vs
0.095, 0.073 vs 0.091). So the gate buys no improvement in the ratio that matters and gives up the
amplitude that has to clear the fee. The consequence is measured, not inferred: **SOLEUR goes from
6 of 24 arms positive ungated to 0 of 24 with the gate on.** A filter for *this* mechanism would
have to select high amplitude at low efficiency, which is not what any gate in this document does.

### The market, measured rather than searched

**There is no exploitable serial dependence at any horizon from 15 minutes to 30 days.** Ungated
Lo-MacKinlay VR is 0.99 / 0.94 / 0.93 / 0.94 / 0.89 / 0.90 / 0.85 at 1 h, 4 h, 12 h, 1 d, 2 d,
5 d, 30 d. The naive contrarian's gross expectation peaks at **+0.74 % per operation at 5 days
against a 0.80 % round trip**, and a shuffled control pays *more* than the real market at every
horizon up to 3 days.

**Over 1 h to 3 d the price path is statistically indistinguishable from a driftless random walk
at every volatility level** (Kaufman efficiency ratio against its `1/√N` null: 0.95-1.04 across
LL…HH). High-ATR stretches are exactly as directional as low-ATR ones.

**No causal feature predicts a fixed-horizon forward return.** Sixteen features in two families —
price/volatility, and **flow** (volume, trade count, mean trade size, signed imbalance; the one
column class no earlier experiment had read) — score AUC **0.43-0.52** at 12 h / 1 d / 3 d.

**The opportunity is there and is unreachable.** Fifty perfectly timed trades on the admitted
bars are worth +393 % to +430 % of base asset in each of 2023-2025 at the real fee. The bot
trades 27-33 times there and captures about **2.5 %** of it.

**The fee is not the binding constraint, measured at zero.** The slow contrarian run as a
long-or-flat bot loses 54-69 % of the base asset in its worst year **with the fee set to zero**;
the whole fee bill is worth 5-15 points at 2 days and 1-3 points at 30, against a sixty-point
gap.

### Inverting the design: what the bot needs, and whether anything delivers it

Measured over 3 988 (gate, year, config) points — 154 variants × 8 years × 4 configs:

**The bot needs the admitted market to fall, monotonically.** Share of cells beating holding by
admitted drift: 29 % below −60 %, then 18 %, 22 %, 12 %, 12 %, 8 %, 4 %, **0 %** above +150 %.
And **more variance is worse, not better** — inside the flat-drift band the share positive falls
from 21 % at 30-45 % annualised volatility to 9 % above 80 %.

**That requirement is deliverable causally, 8 years of 8** — which was expected to fail and did
not. "The market while the price sits below its recent high" has negative drift *by
construction*, so fifteen up-only variants hold admitted drift between −99 % and −10 % in every
year of 2018-2025, with no forecast involved.

**It is necessary and not sufficient.** Two of those exact gates returned 0/105 on the full 1-min
sweep, and the per-cell cross test over 452 (gate, config) pairs gives **0 positive in all eight
years**, best worst-year **−8.5 %** — itself the best of 452, so a pre-committed choice does
worse. The conditioning that makes negative drift deliverable is the same one that hands the
recovery back at the stretch boundary.

---

## Decisions taken

Settled choices with the reason. **Add here whenever something is settled** — this document once
recorded only open questions, lost two decisions, and had both contradicted by work done from it.

### Scoring and method

- **The base asset, not euros, is the objective**, scored on **one continuous run** over the whole
  span. Report the operation count beside every result.
- **Never slice time without saying what happens to the open position.** Chaining segments charges
  entry fees the running bot never pays and liquidates positions it would have kept: chaining
  `bajo max`'s seven yearly factors gives +172 % where the continuous run gives **+27.6 %**, a
  six-fold inflation. It changes the *order*, not only the level.
- **"Beats buy-and-hold" is not a result on its own.** Report where a config lands in the
  distribution of the whole space. In one span, 88 of 105 configs beat holding.
- **The median is the honest estimator only where selection carries no information, and that must
  be checked per regime.** Report the median *and* the forward percentile of a fitted pick; where
  they disagree, the percentile describes production, which runs one config.
- **Judge a family by whether its specific winners repeat on other data, never by its median.** A
  family whose median is −14 % can still contain the one that works. Report the median as
  description, decide on the cross-window table, and always print the base rate beside an "n of n"
  count. *(The owner had to raise this twice.)*
- **Do not select by compounded return over a set of segments.** The compound is dominated by
  whichever segment was largest. Select for a rate — segments won, or a per-segment median.
- **Selection needs segments, not years.** Four segments decide nothing; eighteen transfer.
- **Select each component by its own objective, then fix it before choosing the next.** The gate
  is ranked by admitted drift against coverage (no engine, so eight years are affordable) and then
  held constant while the trailing stop is swept. Choosing both with one return metric over
  154 × 105 combinations is how this study produced three false discoveries.
- **Measure the market before searching it again.** After 154 detectors and 105 configs came back
  negative, the informative move was a variance ratio and a trade-capped perfect-foresight
  ceiling, not another sweep. Prefer a bound or a property over one more search.
- **Treat a difference below the harness's own dispersion as unresolved, not disproved.**
  Resolution alone moves a single config by up to 25 points.
- **Report a frequent rule's break-even fee per leg, not its result at one fee schedule.** A rule
  that trades hundreds of times a year has a cost that scales with its operation count while its
  edge does not, so "loses at 0.40 %, wins at 0 %" says nothing about how far off it is. Invert
  it instead: `f* = 1 − exp(−ln(1 + edge) / operations)`. The VWAP Z-score's best arm needs
  **2.6 basis points** a leg — one number that settles it, where a fee sweep would have produced
  a table and an argument. Print it beside any arm making more than ~50 operations a year.

### Search space and engine

- **One `stop_pct` shared by all five volatility levels.** At the operation counts the winning
  configs produce (2-14 a year), a run exercises two or three levels, so the rest are
  **unidentified** and the search fills them with noise. It also makes the space enumerable
  (21 × 5 = 105), which removed Optuna, TPE, seeds and AUTO. Freeing them was measured first: the
  AUTO search returned four different answers from four seeds after 12 000 trials, and 600
  free-stop draws move the maximum (+73.5 % vs +59.0 %) and nothing else — same median, same
  quartiles, same share beating hold. That maximum is an order statistic of 600 draws from a
  superset.
- **`train_split = 1.0` — no inner train/test split when fitting.** A window that influences which
  config is selected is training, not test.
- **`stop_pcts` grids are capped at 0.9.** `K_STOP` at 1.0 is a sample *maximum* set by one
  observation that drifts as history grows — worth a 35-point swing.
- **The `k_act` branch is disabled in the experiments**, and `k_act = 0` is the worst
  configuration the strategy has: 807 operations in 2025 for a median −95.3 % of base asset.
- **The activation re-anchor stays, on both sides.** Removing it turns the bot into one that sells
  once and waits: on a window where the price does not come back, 105 of 105 configs end in cash
  at −22 % to −25 %. `reanchor_sell` / `reanchor_buy` / `reanchor_cap_at_entry` remain as inert,
  tested switches.
- **The engine takes a calibration *schedule*, not one fixed calibration**, and no coarser cadence
  is safe: at 192 bars instead of 48, `mm=0.035/0.9` moves from +34.3 % to −1.4 %.
- **`MINIMUM_CHANGE_PCT` is an ATR multiple wearing a price fraction.** 0.02 is 8.7 median ATRs on
  XBTEUR and 38.8 on USDCEUR, where it yields ten pivots a year and the five levels are filled
  from the same ten episodes. Expressing it as a multiple of the pair's median `ATR/close` is the
  correct fix and is **unimplemented**.
- **`hodl_pct` is not worth modelling.** Scored in base asset, holding is 0 % by construction, so
  a permanently held fraction is a linear blend between the bot's result and zero: it can only
  move the result *toward* zero. A dilution control, not a lever. *(Owner's call; the algebra
  agrees. An earlier version of this document called it "the one lever that softens a rally" and
  was wrong.)*

### Scope

- **XBTEUR only until the line is defined and built (owner, 2026-09-10).** The USDCEUR
  external-validity check is the most informative *unrun* experiment in the document — configs
  make 11-45 operations there against 3-7 on XBTEUR, the first setting where a predictiveness test
  would have power — and is **deferred, not dropped**.
- **Widened to ETHEUR and SOLEUR for the grid only (owner, 2026-09-11).** The one avenue that
  needs no forecast is also the one where a different pair is a real hypothesis rather than a
  robustness check: the fee is proportional and the amplitude is not, so a pair that moves more
  clears the same toll more often. It measures, and the gain is **sub-linear** — SOLEUR's median
  per-bar excursion is 1.8× XBTEUR's and buys only a 1.4× break-even fee (3.6 bp against 2.6),
  because a larger amplitude also crosses more grid levels and so pays more tolls. One pair
  is not a law, but the direction is the point: *volatility does not buy edge one for one, so
  reaching for a more volatile pair is not a way to close a 10× gap.* Everything else stays
  XBTEUR-only and the USDCEUR external-validity check stays deferred.
- **Run a new experiment on one or two years first, and ask before widening (owner, 2026-09-11).**
  An eight-year run of the gate/config machinery costs over an hour of wall clock, most of it
  rebuilding the O(n²) calibration schedule. `BOTC_POINT_CACHE` makes a repeat on the same frame
  nearly free, so widening after a narrow run is cheap.

---

## Closed avenues

Twenty-four, each a hypothesis about where the edge lived. None was a tuning question.

| Avenue | What closed it |
|---|---|
| Selecting by in-sample PnL | percentile 50 of the forward distribution — chance, over nine dates |
| Reconfiguring on a cadence | worse than not reconfiguring, at every cadence; trailing-window refits land below the bottom quartile |
| Selecting by past consistency | identical to picking at random (3.0/6 either way) |
| Picking a config from the `mm` 0.04-0.07 region | does not replicate at 1-minute resolution |
| Gating the bot through rallies (suppressing sells) | +0.6 points with perfect hindsight |
| Forcing full allocation through rallies | −3.2 points with perfect hindsight: recovers +17.3 in the rallies, gives back −21.4 in the crash after it |
| Asymmetric stop by side | ±0.4 points, and the effect runs against the proposed mechanism |
| Per-side `min_margin` (sell reluctant, buy eager) | −5.5 to −7.0 points median, worsening with the asymmetry; the opposite direction is zero |
| Gating on volatility instead of direction | high-ATR stretches are no more directional than low-ATR ones (ER 0.95-1.04 against the `1/√N` null) |
| Five free per-level `stop_pcts` | the AUTO search does not converge (0/4 seeds after 12 000 trials); 600 free draws move the maximum and no other statistic |
| Immediate activation (`k_act = 0`) | 807 operations in 2025 for a median −95.3 %, 0/5 beating hold, in the one year holding lost money |
| Switching the config by regime, with perfect labels | 18-19 points *below* the best fixed config: the side held when a regime begins decides it, and activation cannot change that side |
| Removing or capping the activation re-anchor | fixes the cycle loss (worst −79.5 % → −1.4 %) and still trails the grid median; on a window where the price never returns, 105/105 sell once and land at −22 % |
| Reverse-engineering the rules from ideal trades | the pivot label is worth +406 % traded perfectly; no causal feature predicts the honest target (AUC 0.43-0.52), and a model scoring 0.767 against the leaky label captures +10.6 of those 406 points at *zero* fee |
| Trading more often to dilute luck | confirmed in the direction that hurts: configs making 100-784 operations converge *below* the passive ones (−93.0 % vs −78.0 %) |
| Replacing the bot with a threshold-rebalanced constant mix | the rebalancing premium is negative in every rising window; −18.4 % over three years, of which 0.8 is fees and 17 is drift. Bounds the whole signal-free family at ~+2 % a year, and only when the market falls |
| Overlaying entry timing on a monthly DCA | buying the whole contribution on day one wins in all three windows; every arm delays exposure against a positive drift |
| The bot's trailing entry as the DCA's buy rule | with a fall requirement, 87 of 96 months enter below the day-one price and it still finishes behind — **a high hit rate with a negative expectation** |
| Holding by default and trading only confirmed ranges | the mechanism verifies on three years, but all of it rests on labels seeing seven days forward; 63 causal rules land between −27.3 % and +0.1 % on held-out 2023 against a +21.8 % ceiling |
| Buying rally precision with recall, or with a stateful ceiling-break rule | the best rule reaches 78 % precision at 17 % rally contamination and still returns −1.1 % and −11.7 % on held-out years. Residual rally exposure of 17 % costs more than 83 % of the harvest earns |
| Fall-harvesting (leaving the falls tradeable) | refuted at its best case: a gate admitting a market falling at ≤ −96 % annualised, run with the fastest config, returns −15.0 / −48.4 / −52.1 %. The symmetric gate that *blocks* falls wins 23 of 36 paired comparisons |
| Any gate at all, including one fitted with hindsight | a shuffled, memoryless path admits a *better* oracle gate than the real market, 4 of 4 cells |
| A VWAP Z-score rule inside the lateral gate | the edge is real, tiny and an order of magnitude below its own cost. The distance to VWAP is the *only* causal feature ever to leave the null band against the honest forward label (AUC 0.436 at 12 h, p = 0.00, 2025 held out) — but so do `ma_dist_1w`, `ret_3d` and `rsi_1d`, and the **unweighted, unnormalised** distance scores the same, so neither the volume weighting nor the z adds anything. Run as a long-or-flat rule over 24 pre-declared arms: at the real fee **0 of 24** positive in both 2024 and 2025, best worst-year −19.0 %; at **zero fee** 3 of 24, best worst-year +4.6 % against a shuffled control's 0 of 24. Those three arms trade 175-334 times a year, so their **break-even fee is 2.6 basis points per leg** — a tenth of Kraken's best maker rate, before any slippage. And the arms slow enough for the fee not to matter (20-34 operations) are negative at zero fee, so trading less does not rescue it |
| A grid harvesting oscillation, on three pairs | the mechanism is sound, the market does not feed it, and the gate feeds it the wrong thing. A grid needs no forecast — only a round trip worth more than two fees, which fixes the minimum viable spacing at **0.8 % taker / 0.5 % maker** before any measurement. 24 pre-declared arms (3 widths × 4 spacings × 2 shapes) rebalancing toward a weekly-median anchor, scored in base asset **with the inventory marked**, on XBTEUR / ETHEUR / SOLEUR over 2024-2025. **Inside the lateral gate: 0 of 24 positive in both years, on all three pairs, at zero fee.** Ungated: 0/24 on XBTEUR and ETHEUR, **6/24 on SOLEUR** (best worst-year +6.5 %) — the volatility argument is real, and it buys a **break-even fee of 3.6 basis points per leg** against the VWAP rule's 2.6. A factor of 1.4 where a factor of 10 was needed, on the pair with the worst spread, at 814-2 543 operations a year. At the real fee, 0 of 24 everywhere, best worst-year −23.8 % |

---

## Measurement traps

Five defects produced confident, wrong numbers here. Four were caught only when a second,
independently written measurement disagreed with the first. **Before trusting a number, ask what
it would look like if the harness were wrong.**

**The common signature of the three look-ahead defects: the number is large, clean, and arrives
before anyone has audited the information boundary.**

1. **A daily flag applied to its own day is 24 h of look-ahead.** Worth more than the entire
   measured edge — a median of +42.9 points across the grid, +58 at 1-min fidelity. Shifted one
   day, 0 of 70 grid points beat holding. *Any rule that reads a bar-derived series must state
   which bar it is applied to and be tested one bar later.* Related: **a gate must be evaluated
   continuously, not on the grid of the series it reads** — daily staleness alone was worth 23
   points.
2. **A mask over the price series leaks unless the leg is reopened when it lifts.** A trailing
   stop that keeps tracking under a gate exits at a level anchored inside the mask, which is the
   oracle's label converted into money. That single switch (`reset_on_unmask`) was worth 159
   points of apparent edge. Any gated experiment must set it and report the share of exits landing
   just after a lift.
3. **A high AUC against a pivot-derived label is not evidence of a signal.** Pivots alternate, so
   "which leg am I on" is knowable from the trailing return and carries the label with it. Check
   any label built from future extrema against a fixed-horizon forward label *and* a money test.
4. **A statistic computed inside a gate's open stretches is computed on a sample the gate
   selected.** A band condition truncates long excursions, so VR < 1 appears on material with no
   memory at all: a shuffled path through the same gate gives VR 0.65 / 0.46 / 0.40 / 0.33 at 1,
   2, 3 and 5 days against the real 0.61 / 0.46 / 0.38 / 0.32. **Any property measured inside a
   gate needs a synthetic control before it is believed**, and sharpening a gate to lower the
   measured VR optimises the artefact.
5. **`Operation.idx` is the operation's ordinal (`len(ops) + 1`), not a bar index.** Resolve an
   operation to its bar through its timestamp. Comparing an ordinal to a set of bar indices
   classifies by coincidence, and the coincidence tracks gate coverage.

Two more about how a comparison is built:

- **An overlay that substitutes a result is not a simulation of the intervention.** Replacing a
  gated period's outcome with holding's reported +21.9 points where re-simulating delivers +0.6,
  because the intervention changes what the bot holds afterwards.
- **A control has to be controlled: same benchmark, same volatility construction, one thing
  changed.** The first oracle-gate control shuffled across the warm-up, so the year's return moved
  and one cell read 5.957 × 10¹⁰ % against a synthetic path that had fallen 99.9 %; it also left
  the real ATR attached to synthetic prices.

And three about what a number means:

- **A signed bet's expectation is not reachable by a long-only bot.** `E[−sign(r_t) · r_{t+1}]`
  pays equally for calling a rise and a fall; the bot can only be long or flat, so the second half
  becomes sitting in cash, which in base asset is the dominant loss during a rise. A +0.742 % per
  operation that looked like a near-miss became a −62 % worst year once converted. **Convert
  first, then compare to the fee.**
- **How a gate is applied to a *sized* rule is a free parameter, and it can manufacture the
  result.** A long-or-flat rule has an obvious reading of a closed gate — hold the asset — but a
  rule that holds *fractions* does not, and forcing it back to 100 % on every close is a trade.
  `impulso` switches 567 times in 2024, which fabricated **287 units of turnover against the 24
  the grid itself generates** — 92 % of the fee bill — and was directionally biased on top: the
  forced repurchases kept dragging the rule back into a rising asset, moving XBTEUR 2024's best
  zero-fee arm from −35.0 % to +0.8 % and 0 of 24 arms to 2 of 24. What gave it away was that the
  average trade moved 44 % of the portfolio where the grid's own step is 8 %. **Freeze on a closed
  gate; never rebalance into one**, and print turnover beside the operation count so the ratio is
  visible.
- **A shuffled control keeps the drift, so its level is not a zero to read against.** The
  permutation does not change the sum of the returns, so the control path ends at the same price
  as the real one and carries the same structural cash drag — a grid sitting at 50 % through a year
  that multiplies by 2.3 prints −29 % having done nothing wrong, in *both* columns. Only the
  **distance** between real and control is interpretable, because serial dependence is the only
  thing the shuffle destroys.
- **A detector that beats its own oracle ceiling is not approximating the oracle.** Report the
  ceiling beside the detector and treat any excess as a different strategy until decomposed.
- **Resolution adds a config-specific term of up to 25 points with no systematic direction.** The
  median delta 1m−15m over the full 105-config grid is exactly **+0.00 %** on both 2024 and 2025,
  with rank correlations +0.798 and +0.938. Gross rankings survive; a single config's number does
  not. *(An earlier claim that 15-minute simulation cost the bot 19 points was a draw from this
  distribution, not a bias, and is retracted.)*

---

## Still not established

- **Whether any data this study does not hold predicts.** Order book, trades tape, funding rates,
  cross-asset. The signal screen exhausted the OHLCVT archives, including the volume and
  trade-count columns nothing else had read. That bounds *these* data, not all data.
- **Whether anything predicts on a pair that trades enough.** Every result here is XBTEUR at 3-7
  operations a run. Deferred by the scope decision above.
- **What slippage costs.** Every arm of every measurement fills at exactly `stop_px`. Production
  places a limit at the market price after detecting the breach and chases it. The term is
  strictly negative and entirely unmeasured.

---

## Production consequences, unimplemented

1. **`MINIMUM_CHANGE_PCT` should be a multiple of the pair's median `ATR/close`**, not a price
   fraction. It is a single global in `trading/market_analyzer.py`; everything measured here was a
   monkeypatch.
2. **Base-asset denomination as a request flag** (`objective: "EUR" | "BASE"`), reporting only.
   Within one window the divisor is constant so the ranking is unchanged; across windows, `min()`
   of differently transformed values does not preserve order.
3. **Schedule anchoring.** `build_calibration_inputs` anchors its points to the window's first
   bar; production recalibrates on its own clock. Frame-anchored is faithful, window-anchored is
   what ships. Undecided, and not cosmetic.
4. **Unify the two activation branches** into one two-dimensional space. It is the reason the
   profitable region only ever existed in one branch. Touches `activation_distance` in both
   `trading/engine.py` and `trading/positions_manager.py`; needs its own validation.

The inert `EngineConfig` switches that remain, all production-neutral by default and covered by
tests: `force_hold_bars`, `reset_on_unmask`, `force_hold_rebuy`, `reanchor_sell`, `reanchor_buy`,
`reanchor_cap_at_entry`, `min_margin_sell` / `min_margin_buy`.

---

## Tools

All read-only, all require `PYTHONPATH=.`. Everything under `scripts/analysis/` is **throwaway
study tooling, not project code**; it reads Kraken's OHLCVT archives directly and needs no
database. Twenty-two scripts were deleted on 2026-09-11 once their question closed — their
results are above and are **not** re-derivable from the tree. What survives is what still answers
a question, or is machinery a successor will need.

| Script | What it answers |
|---|---|
| `scripts/import_kraken_ohlcvt.py` | Loads Kraken's CSV archives into `ohlc_data` (REST only returns ~720 candles). |
| `execution_fidelity.py` | **The shared library, and the resolution question.** Frame loaders, daily closes, Wilder ATR on 15-min bars projected onto the fine grid, the calibration schedule with its `BOTC_POINT_CACHE` disk cache, and `remap`. Everything else imports it. As a script it runs the same configs at 15/5/1 min with ATR and calibration held fixed, so only the evaluation cadence varies. |
| `gate_families_live.py` | **The signal vocabulary.** The `Series` class that aligns any daily-derived quantity to the fine grid, `_confirm` (a confirmation delay before opening, never before closing), and 154 continuous causal detectors across 8 families. As a script, runs them all as single causal arms at 1-min fidelity and reports the per-family distribution and the surface — because the head of a 154-row ranking on one year is an order statistic. `--out` dumps per-variant JSON. |
| `gate_config_sweep.py` | **With a signal fixed, what does the trailing stop want?** The 105-config grid on the bars one named gate admits, 1-min path, ranked by worst year, with the full worst-year surface printed. The gate is a `--gate` choice made beforehand and is never re-picked from the output. **The workhorse for any new signal.** |
| `gate_filter_quality.py` | **Which signal filters best, by the signal's own objective?** Admitted drift (annualised by coverage) against coverage, per year, per variant, with no engine at all — so eight years run in minutes. Coverage-band ranking, because a Pareto count would rank the always-open gate top. |
| `gate_families_cross.py` | **Do the variants that win here win there?** Crosses per-variant JSON from several runs, ranks by windows won with the *worst* window as tiebreak, and prints the base rate an "n of n" count would reach by chance. The test that replaces judging a family by its median. Seconds. |
| `gate_requirement.py` | **What must a signal admit for the bot to win, and can any deliver it?** Runs the bot over what every variant admits across years and configs, maps the result against the admitted market's drift, volatility and coverage, then cross-tests per (gate, config) cell in *every* year. 15-min screen. |
| `lateral_horizon.py` | **Is there exploitable serial dependence, and does it pay?** Lo-MacKinlay VR counting only aggregation windows contained entirely inside one open stretch, out to 30 days; the naive contrarian's gross expectation on non-overlapping returns against the round-trip cost; and, decisively, a **shuffled-path control**. Three arms: gated, ungated, shuffled-gated. **Any mean-reversion model needs this control.** |
| `lateral_market_structure.py` | **Is there anything to capture at all?** Trade-capped perfect-foresight ceiling (exact two-state DP with an operations dimension, so the bound answers "what could an N-trade mechanism get" rather than the useless uncapped infinity), plus the stretch-length distribution against the round-trip cost. Also provides `stretches`. |
| `gate_ceiling.py` | **Could ANY signal make the bot profitable?** Represents the gate as one boolean per day and hill-climbs it against the bot's result with the whole year visible — a bound on every gate, not a strategy — beside the same hill-climb on a shuffled version of the year with OHLC, ATR and the holding benchmark all rebuilt to match. Only the real-minus-shuffled difference is interpretable. |
| `signal_screen.py` | **Does anything visible at `t` predict, and is it worth money?** Three parts that must all pass: what the perfect label is worth traded, per-feature AUC against both that label and a fixed-horizon forward label, and the model's prediction run as an allocation in base asset at zero and maker fees. Circular-shift null, temporal split fixed in advance. **The first test any new signal should face.** Twenty features in three families; the VWAP family carries the raw distance beside the z so any effect can be attributed to the volume weighting or to the normalisation, and part 3 prints its own null band — the first version did not, which left a 0.43 unreadable. |
| `vwap_zscore_rule.py` | **Does a threshold rule on a signal actually pay, long-or-flat?** The step `signal_screen.py` cannot take: a hysteresis rule (sell at z ≥ +k, rebuy at −k or at the anchor) scored in base asset, at zero fee *and* the real one, with the shuffled control beside it and the operation count that sets the break-even fee. Gated and ungated, 24 pre-declared arms, whole distribution printed. The state path is a forward-fill of the last directive (the hysteresis is idempotent), so it vectorises and the fee is charged afterwards on the flips — the same path serves every fee. **The template for scoring any future signal as a rule.** |
| `grid_roundtrip.py` | **Is there oscillation worth harvesting, and on which pair?** The only test here that needs no forecast: a grid rebalancing toward a weekly-median anchor, scored in base asset **with the inventory marked** — scoring only the realised round trips is the whole illusion — gated and ungated, at zero fee and the real one, with the shuffled control beside it and the break-even fee solved by bisection. 24 pre-declared arms across three pairs. Trade times are fee-independent (after a rebalance the actual fraction equals the target regardless of what was paid), so the expensive scan runs once per arm and every fee is charged on the same path. Also prints the two diagnostics that explain any result it produces: median per-bar amplitude and the Kaufman efficiency ratio, inside the gate against outside it. **Use this before proposing any mean-reversion mechanism; it is cheaper than the rule that implements one.** |

**Caching.** A calibration schedule over a long window costs 280-640 s to build (O(n²): each point
re-analyses history up to its own bar). Set `BOTC_POINT_CACHE` to a directory and it is paid once
per frame. A new harness should use it and should print progress — a silent six-minute build looks
like a hang.

---

## Activation math (reference)

```
k_act branch:      distance = k_act × ATR
min_margin branch: distance = K_STOP × ATR + min_margin × reference_price
K_STOP(level)    = ceil( quantile(K_values_level, stop_pct) × 10 ) / 10
```

`K_values` are the per-leg, per-level maxima of `drawdown/ATR` (uptrend → sell side) or
`bounce/ATR` (downtrend → buy side).

`reference_price` is **not** the entry price after re-anchoring. `activation_distance` is
re-evaluated against the *current* price whenever the gap exceeds the expected distance, so a sell
position's target follows the price down and `min_margin` is a **barrier width, not a profit
floor**. CLAUDE.md describes `PAIR_MIN_MARGIN` as a margin "from entry"; that is accurate only
until the first re-anchor.
