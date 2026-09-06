# Optimizer Validation — Design and Study State

Status (2026-09-06): **four defects fixed, one harness defect fixed — and three avenues
closed by measurement: config selection carries no forward signal, reconfiguration makes it
worse, and gating the bot through rallies pays +0.6 points even with perfect hindsight.** The question
this document exists to answer is unchanged — *can the optimizer produce a config that
beats buy-and-hold out of sample?* — but the answer it carried before 2026-09-02 rested on
measurements that were wrong. Everything PnL-based in the
previous version of this file (`optimizer-grid-derivation-design.md`) is retracted; see
"Retracted".

The framing of the question has since changed too, and the change matters more than any
single number: **the goal is accumulating the base asset, not euros.** See "The objective
is asset accumulation".

One correction runs through this file's history and is worth stating up front: the frame was
repeatedly described as "one 15-month bear market". It is not — it holds a 23 % fall, a 33 %
rally, a flat stretch and a 40 % crash. Reading the endpoint return as the regime hid the
single most important result, which is what the bot does in the rally.

This file supersedes that one. It is the handoff: read "Where the study stands",
"Decisions taken" and "How to continue" first.

A note on how this document failed once already: it recorded open defects and questions
faithfully but **did not record decisions that had been taken**, so two of them were lost
and later contradicted by work done from this file alone. "Decisions taken" exists to stop
that recurring. Add to it whenever something is settled.

## The question

The bot must beat holding the asset. Not "be profitable" — in a rising market anything
long is profitable, and in a falling one avoiding the market looks like skill. The only
figure that means anything is the **euro value of the portfolio against buy-and-hold over
the same window**, measured out of sample.

Everything below is XBTEUR, 15-minute candles, **0.4 % fee per leg** (the bot's limit
orders usually fill as maker; the 0.8 % taker figure used earlier overstated the cost by
2×). The window with continuous data is **2025-01-01 .. 2026-03-31** (43 610 simulated
bars). Buy-and-hold returns **−35.1 %** end to end — but that number describes the two
endpoints, not the path, and reading it as "one bear market" was wrong. The frame contains a
full cycle:

| Stretch | Move | Phase |
|---|---|---|
| Jan–Mar 2025 | 98 889 → 76 292 (−23 %) | fall |
| Apr–Jul 2025 | 76 292 → 101 385 (**+33 %**) | **rally** |
| Aug–Oct 2025 | 101 385 → 94 881 (−6 %) | flat |
| Nov 2025–Feb 2026 | 94 881 → 56 786 (**−40 %**) | crash |

Global high 107 361 (2025-10-06), global low 51 950 (2026-02-06): +107 % low-to-high and
−45 % high-to-end, inside the frame. Monthly volume ranges 8 632 to 29 441, a factor of 3.4,
so market structure shifts materially even within these fifteen months.

Two corrections to how that window used to be described here. The ~10 700 candles in
`ohlc_data` beyond those 43 610 are **more recent than 2026-03-31, not older than 2025** —
about 111 days, separated from the frame by a gap, which is why the frame ends there. They
are the bull-regime data task 2 needs. And the frame is not perfectly continuous: it holds
**8 gaps, the largest 27 candles**. That is ~50 bars in 43 610, negligible for
position-indexed arithmetic (a "90-day" boundary lands a few hours late), but it is not
zero and a harness that indexes bars by position should keep reporting it.

## Where the study stands

### The single most important number

**Selecting a config by its in-sample PnL lands at median percentile 50 of the forward
distribution — chance.** Measured over nine decision dates by enumerating the whole space.
See "Fitting does not predict"; everything else here is subordinate to it.

Two supporting facts that change how the older results read:

- Over 2025-04-01 .. 2026-03-31 (364 d, hold −23.68 %), **88 of the 105 configs beat
  buy-and-hold** and the median config accumulates +22.3 % BTC. So in that window "beats
  buy-and-hold" is nearly free, and every earlier result phrased as "+N points vs hold" is
  far weaker evidence than it reads. **Report where a config lands in the distribution of
  the whole space, not whether it clears hold.**
- That fraction is itself span-specific: in the later windows only 13, 14 and 18 of 105
  beat hold. The space is not uniformly good; it is good in some spans and bad in others,
  and which is which is not knowable in advance from the fit.

Both are only knowable because the space is now small enough to enumerate (105 configs),
which is itself a consequence of two decisions recorded below.

### Task 1 — the honest out-of-sample test, re-run (2026-09-04)

Fit on the first N days, score the winner on **one continuous run** over the entire
remainder, in euros, against hold over the same span. `scripts/analysis/holdout_experiment.py`.
Five free `stop_pcts`, both branches, `min_margin` ≤ 0.10, 3 seeds:

| Fit window | Median result | Hold | vs hold | Seeds beating hold |
|---|---|---|---|---|
| 60 d | −18.3 % | −29.0 % | +10.7 | 3/3 |
| 120 d | −24.0 % | −29.9 % | +5.9 | 3/3 |
| 180 d | −11.7 % | −35.7 % | +23.9 | 3/3 |
| 240 d | −30.9 % | −37.4 % | +6.5 | 3/3 |

**12 of 12 beat hold**, which retracts the earlier "no optimized config generalizes".
Read it against the distribution above, though: beating hold is what most of the space
does. What this table does establish decisively is **defect 3** — every one of the 12
winners chose `min_margin` between 0.030 and 0.090, so **not one of them existed inside the
old ≤ 0.010 grid**. The search space, not the market, was the binding constraint.

Two weaknesses, both since addressed: 7 of 12 winners pinned at `mm=0.090`, one step under
the ceiling; and seed variance at 60 d spanned 51 points (−22.6, −18.3, +28.8) with only
2–6 ops for the pinned configs, so the "profitable" picks were mostly quiescence.

### Reconfiguration cadence (2026-09-05)

`scripts/analysis/refit_frequency_experiment.py`, one continuous run per arm over a single
shared forward span (2025-04-01 .. 2026-03-31, hold −23.68 %), shared `stop_pct`, no
`k_act` branch, `min_margin` ≤ 0.20:

| Cadence | fijo | expansivo | reajuste |
|---|---|---|---|
| 30 d | **+5.51 %** | +1.43 % | −21.84 % |
| 60 d | **+5.51 %** | +0.41 % | −16.87 % |
| 90 d | **+5.51 %** | −3.80 % | −16.88 % |

In BTC: fijo **+38.3 %**, expansivo +26 to +33 %, reajuste +2.4 to +8.9 %, hold 0 %.

`fijo > expansivo > reajuste` in all nine comparisons. Placed against the distribution of
the whole space, the ordering says something sharper than "don't reconfigure":

- fitting once lands at **percentile 93**
- re-fitting on all history lands around the **upper quartile**
- re-fitting on a trailing 90-day window lands **below the bottom quartile** — worse than
  75 % of configs drawn at random. Frequent re-fitting on recent data destroys value; it is
  not merely suboptimal.

Two controls hold and are worth keeping in any successor harness: `fijo` never re-fits, so
its number must come out identical at every cadence (it does), and a zero-op arm must sit
**one** entry fee below hold rather than one per segment (−0.36, not −0.36 × segments).

### Fitting does not predict (2026-09-05)

**This is the central result, and it invalidates selection by in-sample PnL.**

`scripts/analysis/grid_sweep_holdout.py` enumerates all 105 configs in-sample and again on
the forward span, reducing the question to a rank: where does the in-sample winner land in
the forward distribution? Chance is percentile 50.

| Decide | Forward | Config chosen | BTC forward | Percentile | Beat hold |
|---|---|---|---|---|---|
| 2025-04-01 | 364 d | `mm=0.040 s=0.9` | +38.3 % | 93 | 88/105 |
| 2025-05-01 | 334 d | `mm=0.030 s=0.6` | −1.9 % | 12 | 89/105 |
| 2025-05-31 | 304 d | `mm=0.040 s=0.8` | +17.2 % | 64 | 71/105 |
| 2025-06-30 | 274 d | `mm=0.200 s=0.5` | −0.4 % | 35 | 67/105 |
| 2025-07-30 | 244 d | `mm=0.200 s=0.5` | −0.4 % | 82 | 18/105 |
| 2025-08-29 | 214 d | `mm=0.150 s=0.5` | −0.4 % | 50 | 53/105 |
| 2025-09-28 | 184 d | `mm=0.150 s=0.5` | −0.4 % | 46 | 56/105 |
| 2025-10-28 | 154 d | `mm=0.030 s=0.5` | −6.2 % | 13 | 13/105 |
| 2025-11-27 | 124 d | `mm=0.080 s=0.9` | −0.4 % | 87 | 14/105 |

**Median percentile 50**, spread 12–93. Pooling the ten best in-sample configs at every
date (n = 90) also gives median 50. The in-sample optimum carries **no information about
the forward span**: it is a draw.

That retracts the single-date reading recorded here earlier, which took the 93 at
2025-04-01 as evidence that fitting predicts. It was one draw from a distribution centred
on chance. It also re-explains the cadence result: `fijo` did not win because fitting once
is better, it won because that one draw happened to be good.

Three further readings:

- **From 2025-06-30 the winner is quiescent and pinned at the ceiling.** Five of nine dates
  choose `mm=0.200` (the ceiling itself) or `mm=0.150`, and their forward result is exactly
  −0.4 % BTC, the zero-operation value. Widening the ceiling from 0.10 to 0.20 did not stop
  the pinning, it moved it.
- **"88 of 105 beat hold" is a property of one span, not of the space.** In the later
  windows only 13, 14 and 18 of 105 beat hold. Quiescence scores percentile 82–87 there —
  not from skill, but because most configs do worse than holding in those spans.
- **The landscape is rugged at grid resolution.** `mm=0.040 s=0.9` is percentile 93 while
  its neighbour `mm=0.040 s=0.8` is percentile 44.

**What remains open is a different question**, and it decides whether anything here is
deployable: does a config that did well in one window tend to do well in the next? If rank
persists, selection should target cross-window stability instead of in-sample PnL. If it
does not, no objective repairs this and the problem is the strategy, not the optimizer.
`scripts/analysis/config_stability.py` measures it as the rank correlation between disjoint
consecutive windows.

### An asymmetry worth explaining

A single fit lands at median percentile 50, i.e. chance. Yet twelve sequential re-fits
(`reajuste`) land *below the bottom quartile* — worse than chance. If each choice were
merely uninformative, repeated choices should average toward the median, not below it.

So the damage appears to be in **changing** the config, not in choosing it. Two candidate
mechanisms, untested: a config change mid-position strands the bot between an activation
barrier it was working toward and a new one, or trailing-window fits select actively bad
configs rather than random ones. Worth resolving before any reconfiguration feature ships.

### Config quality does not persist either (2026-09-05)

`scripts/analysis/config_stability.py`: all 105 configs run once over 2025-04-01 ..
2026-03-31, that single run split into six ~60-day periods by the ratio of its compounded
growth factors, and only ranks *within* a period compared.

Rank correlation between consecutive periods: **−0.19, +0.25, +0.25, +0.11, +0.01** —
median +0.11, and +0.24 across all fifteen pairs. A config's standing in one period explains
between 1 % and 6 % of its standing in another. **There is nothing to select on.**

The apparent winner is an artefact of averaging a trend. `mm=0.130 s=0.5` has the best
median percentile (64) but runs 57, 47, 52, 96, 87, 71 across the six. And the head of that
table is the whole high-`min_margin` family — configs that are rarely excellent and rarely
terrible, so their median rank flatters them. That is "loses little", not skill.

**The one signal that does persist is negative:** `mm` at 0.000–0.010 sits at percentile
0–2 in all six periods. Trading constantly is reliably ruinous. True, useful only as "do not
overtrade", and it inflates the correlations above — strip the obviously bad region and
persistence among the plausible configs is closer to zero still.

**The finding that matters is what the periods do, not what the configs do:**

| Period | Best | Median | Worst | Beat hold |
|---|---|---|---|---|
| Apr–May | +0.6 % | −8.1 % | −40.0 % | 7/105 |
| Jun–Jul | **−3.7 %** | −11.1 % | −25.1 % | **0/105** |
| Aug–Sep | +11.3 % | +7.5 % | −16.0 % | **100/105** |
| Oct–Nov | +33.2 % | +27.5 % | −35.4 % | 92/105 |
| Dec–Jan | +23.9 % | −0.4 % | −25.7 % | 48/105 |
| Feb–Mar | +16.6 % | −0.0 % | −39.8 % | 14/105 |

(base asset accumulated; hold is 0 % by construction.)

The space moves in a block. In Jun–Jul **not one** of the 105 configs accumulates base
asset; in Aug–Sep 100 of 105 do. The median swings 38 points between periods, while inside a
period the gap from median to best is about 6. **Whether the bot accumulates is decided by
the market period, not by the parameters** — which is also why in-sample fitting cannot
predict: it fits whichever regime happened, and the next one is different.

That makes regime the dimension worth measuring, and it is measured in one value only. See
"How to continue".

### The rally is the problem (2026-09-06)

The bull-regime test does not need new data: the frame already contains a 33 % rally. Cross
the six stability periods with the phase each one landed in, and the answer is unambiguous.

| Period | Hold € | Phase | Median BTC | Beat hold |
|---|---|---|---|---|
| Apr–May | **+20.4 %** | **rally** | −8.1 % | 7/105 |
| Jun–Jul | **+11.8 %** | **rally** | −11.1 % | **0/105** |
| Aug–Sep | −5.3 % | flat | +7.5 % | 100/105 |
| Sep–Nov | −19.6 % | crash | +27.5 % | 92/105 |
| Nov–Jan | −10.1 % | crash | −0.4 % | 48/105 |
| Jan–Mar | −17.6 % | crash | −0.0 % | 14/105 |

**In a rally the bot destroys base asset, and in Jun–Jul not one of the 105 configs beat
holding.** In the flat and topping phases it accumulates strongly. That is what an
alternating asset/cash strategy must do — a sustained trend takes it the wrong way — but it
is now measured rather than assumed, on the same continuous run as everything else.

It also explains the results that looked encouraging: the 364-day span carrying +22.3 %
median BTC contains both the rally (bad) and the crash (good), and the crash outweighed. Over
a span that is net *up*, the same space would be expected to lose.

**The consequence for the whole study.** Beating hold requires either not trading during
up-trends — which needs a forward regime signal — or accepting rally losses and relying on
chop and downtrend gains to outweigh them, which is a bet on the market's net direction, not
an edge. So the question has moved from "which config" to "can the regime be seen in advance".

**And the one candidate signal has no measured predictive power.** Choppiness Index over the
bars ending before each of twelve disjoint periods, against what the space did in that
period: **−0.13** (median config), **−0.17** (best config), **−0.24** (share beating hold).
All near zero, all in the *opposite* sign to the backlog card's retracted claim. With n = 12
none is significant, but there is no evidence here that CI anticipates the phase. The phase
matters enormously; this detector does not detect it.

### Trend filtering is dead (2026-09-06)

The rally is where the strategy loses, so the next question was whether blocking the bot
during up-trends could pay. It was bounded before it was built, and the bound says no.

`EngineConfig.no_sell_bars` suppresses the *sell* side on chosen bars: in an up-trend the
damage is leaving the asset and rebuying higher, so the exit is the harmful action, while a
re-entry is what gets the bot back in and must never be gated. The stop keeps trailing while
masked, so the mask defers an exit rather than cancelling it. Empty in production.

Three variants, same continuous run, 105 configs, base asset accumulated over
2025-04-01 .. 2026-03-31:

| Variant | Median | Best | Worst | Beat hold | Bars gated |
|---|---|---|---|---|---|
| ungated | +22.3 % | +52.7 % | −89.1 % | 88/105 | 0 |
| **oracle (realised rallies)** | **+22.9 %** | +52.2 % | −77.1 % | 96/105 | 25 % |
| detector (trailing 30 d > 0) | +22.4 % | +60.6 % | −63.7 % | 90/105 | 44 % |

**A gate that knows in advance exactly which periods will rally adds +0.6 points to the
median.** The causal detector adds +0.1. There is no prize to claim, so no classifier is
worth building — which is what the oracle was for.

Both gates do compress the distribution: the worst config improves from −89.1 % to −77.1 %
and −63.7 %, and the share beating hold rises to 96/105. That is variance reduction, not
edge, measured on one span, and it should not be read as a result.

**A methodological correction, and it is the important part.** A first version of this
oracle worked as an overlay: it replaced a rallying period's result with hold's (0 %) rather
than re-simulating with exits suspended. That version reported **+21.9 points**. The honest
re-simulation reports **+0.6**. The overlay was not a bound at all — substituting a result
is not the same quantity as simulating the intervention, because the intervention changes
what the bot holds *after* the gated stretch, and everything downstream depends on that.

Scope of the negative result: it says suppressing *exits* during rallies does not pay. A
stronger intervention — forcing full allocation during a rally — is untested, but that is no
longer a filter on this strategy, it is market timing, and the regime is not predictable
here anyway.

### The `hodl_pct` question, closed

An earlier version of this file proposed teaching the engine `hodl_pct` and called it "the
one lever that softens a rally without predicting it". That was wrong, and the reason is
arithmetic. A fraction `h` that is never sold has a constant base-asset count, so

```
total accumulation = (1 - h) x accumulation of the traded fraction
```

It is a volume knob, not a cushion: it shrinks the rally loss and the downtrend gain by the
same factor, so it can never change the sign of the edge. Simulating it adds nothing that
scaling the result would not, and the untraded fraction's performance is the price chart.
The engine's omission is therefore not a divergence worth fixing, and the task is dropped.

### Still not established

- **One pair.** XBTEUR only. This is now the main external-validity gap, the regime one
  having been closed.
- **Whether the strategy can beat hold at all**, given that it needs a forward regime
  signal and the only candidate tested has none. See "The rally is the problem".
- **What a non-zero `hodl_pct` would do.** Production can hold a fraction of the target
  allocation permanently; the engine cannot model it (see the divergence below), so nothing
  here measures the one lever that would soften a rally without predicting it.

## Decisions taken

Settled choices, with the reason. **Add to this list whenever something is settled** — two
of these were decided in earlier sessions, were never written here, and were consequently
contradicted by later work that had only this document to go on.

- **One `stop_pct` shared by all five volatility levels, not five searched independently.**
  Reduces the space from 6⁵ to 6 per branch and buys reliability. The evidence since: at
  the operation counts the winning configs actually produce (2–14 ops over a year), a run
  exercises at most two or three levels, so the other levels' `stop_pct` values are
  **unidentified** — the search fills them with noise and the selection then treats that
  noise as signal. Restated: the "harness defect" below claiming five-free is the truth and
  one-shared understates search difficulty is **wrong**, and is retained only as a record.
- **`train_split = 1.0` — no inner train/test split when fitting.** A window that
  influences which config is selected is not a test window, it is part of training. The
  honest test is the forward span, not an inner slice. The consequence to keep in view is
  that the selection is then a pure in-sample optimum with no internal guard, which is
  exactly why "does fitting predict?" has to be measured directly.
- **The `k_act` branch is disabled in the experiments.** It won none of the 12 hold-out
  fits, and defect 5 explains structurally why. Dropping it hands the whole trial budget to
  `min_margin`. This is an experiment scope decision, not the strategy change in defect 5.
- **Scoring is one continuous run, never restarted segments.** See the harness defect, now
  fixed in the engine.
- **The base asset, not euros, is the objective.** See the next section.

## The objective is asset accumulation

The bot's purpose is to end holding **more of the base asset**, not more euros. This was
not previously written down, and it changes what the optimizer should maximize.

Because the portfolio is either base asset or cash, and any cash left at the end is valued
at the final price:

```
asset_final / asset_initial = (1 + r_bot) / (1 + r_hold)
```

What it does change:

- **Buy-and-hold is exactly 0 % in every regime.** The benchmark stops depending on which
  way the market went, so results from a bear window and a bull window become directly
  comparable. This is the reason to adopt it.
- **Reporting stops flattering the bot.** "−11.7 % but hold was −35.7 %" reads as failure
  and is a 37 % gain in base asset; "+5.5 %" reads as success and is +38.3 %.

**What it does not change — a correction to an earlier version of this section.** It was
written here that denominating in the base asset stops the search selecting quiescence.
That is wrong. The transform is `(1 + r) / (1 + r_hold)`, and within a single fit window
`r_hold` is **the same constant for every candidate**, so the ranking — and therefore the
selected config — is identical. Under `train_split = 1.0` the base-asset objective selects
exactly what the euro objective selects.

Two places it does alter selection, and they are the reasons to implement it rather than
merely report it: the split objective `min(train_pnl, test_pnl)`, where the halves have
different holds, and any future objective that compares across windows (see "How to
continue", where cross-window stability is the candidate).

So no result in this document is invalidated by the change of denomination — only
reinterpreted.

Proposed shape: a denomination flag on the request (`objective: "EUR" | "BASE"`), not a
separate mode. The live-bot equivalent is the deferred *Portfolio-vs-Hold Benchmark*
backlog card, which needs a portfolio value time series that is not recorded today.

## The six wrong assumptions

Ordered by size of effect. Four are fixed; two remain.

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

### 3. The `min_margin` ceiling made buy-and-hold unreachable — FIXED (`bef291c`)

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

**Fixed**, and confirmed to have been the binding constraint: all 12 task-1 winners chose
`min_margin` in 0.030–0.090, none of which the old grid contained. The ceiling went to 0.10
first, then to **0.20** when 7 of those 12 pinned at 0.090; the step is **0.01**, since with
a shared `stop_pct` the space is 21 × 5 and finer resolution only makes seeds disagree.
Winners have since moved off the boundary (0.040–0.050), so 0.20 currently holds.

### 4. `stop_pct = 1.0` is a sample maximum, not a percentile — FIXED

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

**Fixed:** every harness caps `stop_pcts` at 0.9. The floor of 0.5 is untouched by this
finding and stands — below roughly 0.5 the stop sits under the median retracement already
observed, so ordinary noise takes it out by construction. That floor argument is reasoning,
not measurement; probing it is a cheap separate sweep, and must not be folded into a run
that is varying something else.

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

### 6. The objective is absolute PnL, never relative to the benchmark — RESOLVED IN PRINCIPLE, unimplemented

`robust_pnl = min(train_pnl, test_pnl)` over `mark_to_market`. Buy-and-hold appears
nowhere. In a trending window this selects for exposure rather than skill: in this bearish
window "do almost nothing" scores well, and in a bull window the opposite would.

It matters less than it looks, because `min_ops` defaults to 0 and a config that never
activates *is* buy-and-hold — the engine's first operation is always the opening buy, and
`mm=0.500` scores exactly −35.07 %, the same as hold minus one entry fee. So the
optimizer's in-sample result is **floored by buy-and-hold**, but only once the space can
express quiescence, which is defect 3.

Defect 3 was fixed and the search kept running to the quiescent corner anyway: 7 of 12
task-1 winners at `mm=0.090`, then 5 of 9 sweep dates at `mm=0.200` — the ceiling itself —
with zero forward operations. Widening the bound moved the corner rather than removing it.

**Denominating in the base asset does not fix this**, contrary to what an earlier version
of this section claimed: within one fit window the divisor is a constant, so the ranking
and the selected config are unchanged. See "The objective is asset accumulation".

The corner is also not obviously a distortion. In a window where most configs lose more
than holding, "do nothing" genuinely *is* the in-sample optimum — the honest answer to the
question asked. The real problem is one level up: that answer does not generalise, because
**no** in-sample answer generalises here (see "Fitting does not predict"). An objective
tweak cannot repair a selection procedure with no forward signal. `min_ops` exists and
would ban the corner, but banning the in-sample optimum is not the same as finding a
config that works.

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

**FIXED (`f130a52`, `afe7077`).** The restart was never a harness choice: `EngineConfig`
held `min_margin` as a scalar and `simulate_operations` always starts flat, so changing a
config mid-history *meant* calling it again. `EngineConfig.activation_schedule` now carries
`(bar, ActivationParams(k_act, min_margin))` as a step function, the way
`calibration_schedule` already carried the calibration — a new `stop_pct` needed nothing,
since the scheduled `PairCalibration` values are what the percentile produces, but
`min_margin` multiplies each bar's price where `k_stop` multiplies its ATR and cannot be
folded in. Production sets neither schedule, so the live path is unchanged.

This mattered most for the cadence question specifically: the number of restarts scales
with the cadence under test, so the artifact landed on the variable being measured and
penalised exactly the arm that reconfigures most.

**The in-tree schedule anchors to the window, production anchors to its own clock.**
`build_calibration_inputs` places its points at multiples of `recalib_bars` from the
*window's* first bar. The live bot recalibrates on a fixed cadence regardless of where an
analysis window happens to start. For a full-history run the two coincide; for a sliced
job they do not. `scripts/analysis/refit_frequency_experiment.py` anchors to the frame,
which is the faithful choice. **Open: decide whether to move the in-tree builder to a
frame-anchored grid.** Given defect 2's sensitivity measurement, this is not cosmetic.

**~~The harnesses collapse the five per-level `stop_pcts` into one shared value.~~** —
**WITHDRAWN.** This was recorded as a defect because the deployed `SearchSpace` searches
five independently (6⁵ = 7 776 per branch), so a shared stop looked like it understated the
search difficulty. That inverts the actual decision, which is that five free values is the
thing to fix: a run producing 2–14 operations exercises two or three volatility levels, so
the rest are unidentified and the search fills them with noise. Kept here as a record of
how the decision was lost. See "Decisions taken".

The consequence is worth stating in its own right: **with one shared stop and no `k_act`
branch the space is 21 × 5 = 105 configs, small enough to enumerate.** That makes the
sampler, the seeds, and AUTO's convergence machinery unnecessary for these experiments —
105 deterministic evaluations replaced 240 (3 seeds × 80 trials) that did not even cover
the space. If shared stops also ship to production, the same collapse applies to the
deployed optimizer, and Optuna/TPE/AUTO stop having a function there too. That is a
significant architectural simplification and has not yet been decided.

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

- **The Trend/Chop card's `+0.44` chop-versus-edge correlation** (`docs/BACKLOG.md`), and
  with it `scripts/analysis/regime_filter_screen.py`, now deleted. It was measured
  2026-09-01, *before* both engine fixes shipped — the cash leg was still paid as if the bot
  held a short, worth up to +162 points of overstatement on exactly the low-frequency configs
  it used, and nothing recalibrated. Two method faults compound it: every 7-day window was a
  fresh `simulate_operations`, so a config whose barrier takes weeks to cross was scored as
  having done nothing; and windows slid by one day, so "75 windows" over the 111 days
  available are about eleven independent weeks. A `+0.44` on that n is not a finding. The
  Choppiness Index itself moved into `config_stability.py`, where it is measured without
  restarts. **Do not cite the +0.44 until it is re-measured.**

**Still standing:** the structural argument for `MINIMUM_CHANGE_PCT = 0.020` (lowering it
redefines what counts as noise rather than sampling more of it); the `stop_pcts` floor of
0.5; that per-level fitted grids are unnecessary because `stop_pct` is scale-free; that
`n = 0` levels are the live `get_k_stop` fallback's job, not the grid's.

## How to continue

In order.

1. **Does config quality persist between windows?** `scripts/analysis/config_stability.py`.
   With in-sample selection dead, this is the question that decides whether anything is
   deployable. If the rank correlation between disjoint consecutive windows is meaningfully
   positive, then a robust config exists and the search should select for cross-window
   stability — a different objective, not a tweak of the current one. If it is near zero,
   no objective repairs this and the problem is the strategy or its parameterisation.
   Everything below is subordinate to the answer.
2. **Decide whether to keep looking, and where.** Three avenues have now been closed by
   measurement — config selection, reconfiguration cadence, trend filtering — and what
   remains is not a tuning question. The one structural idea this study has not tested is an
   **asymmetric stop by side**: `k_stop_buy` and `k_stop_sell` are already calibrated
   separately, and the shared-`stop_pct` decision collapsed them onto one percentile. A wider
   sell-side percentile biases the bot toward staying in the asset *without predicting
   anything*, which is the structural version of what the trend filter tried to do causally.
   Cheap to test on the existing space, but it is a strategy change and needs its own spec.
3. **Test a second pair.** Everything here is XBTEUR. This is now the only external-validity
   gap left, and the cheapest remaining way to learn something new.
4. **Implement the base-asset denomination** as a request flag (`objective: "EUR" | "BASE"`).
   Reporting only, until an objective compares across windows again — see that section for
   what it does and does not change.
5. **Decide whether shared `stop_pct` ships to production.** If it does, the deployed space
   becomes enumerable and Optuna/TPE/seeds/AUTO can be removed — a large simplification of
   `trading/optimizer/`. That decision needs its own spec.
6. **Unify the activation branches** into one two-dimensional space (defect 5). Strategy
   change: it touches `activation_distance` in both `trading/engine.py` and
   `trading/positions_manager.py`, and needs its own validation. Lower priority now that
   `k_act` is out of the experiments, but it is still the reason the profitable region only
   exists in one branch.
7. **Decide the schedule anchoring** (harness defects). Frame-anchored is faithful;
   window-anchored is what ships.

Do **not** revisit `MINIMUM_CHANGE_PCT` or chase pivot density; neither addresses any
defect above. Do **not** report a result as "beats buy-and-hold" without saying what
fraction of the whole space also beats it — in the 2025-04..2026-03 window that fraction is
88/105, and the phrase carries almost no information there.

## Tools

All read-only, all require `PYTHONPATH=.`. Everything under `scripts/analysis/` is
**throwaway study tooling, not project code**: it exists to answer the questions in this
document and can be deleted once they are answered. The harnesses that take `--csv` read
Kraken's OHLCVT archives directly and need no database at all.

| Script | What it answers |
|---|---|
| `scripts/import_kraken_ohlcvt.py` | Loads Kraken's CSV archives into `ohlc_data` (REST only returns ~720 candles). |
| `scripts/analysis/config_stability.py` | **Does config quality persist, and does the regime predict it?** Runs all 105 configs once, continuously, then splits that single run into N periods by the ratio of compounded growth factors. Reports the rank correlation between periods, and the Choppiness Index of the bars before each period against what the space did in it. `--csv`. |
| `scripts/analysis/grid_sweep_holdout.py` | **Enumerates all 105 configs** in-sample and forward, and reports where the in-sample winner lands in the forward distribution, at several decision dates. No sampler, no seed. Reports euros and base asset. `--csv`. |
| `scripts/analysis/holdout_experiment.py` | Task 1: fit on the first N days, score on one continuous run over the remainder against hold. `--csv`. |
| `scripts/analysis/refit_frequency_experiment.py` | Reconfiguration cadence, several cadences per run over one shared forward span, each arm on one continuous run. `--csv`. |
| `scripts/analysis/objective_experiment.py` | 2×2: inner train/test split vs the fit window's own PnL, five free `stop_pcts` vs one shared. **Both factors are now settled — see "Decisions taken" — so this is superseded.** |
| `scripts/analysis/grid_derivation_explore.py` | Reports the structural distributions behind each grid (K per level, leg/ATR, ATR/price). |
| `scripts/analysis/grid_validation.py` | Edge-pinning, coverage, AUTO convergence. **Its `walkforward` mode uses the segment-restart method — see Harness defects; do not trust its chained figures.** |

The point caches exist because the in-tree `build_calibration_inputs` recomputes on every
call, and a schedule over a long window costs minutes — 280–640 s for the 15-month XBTEUR
frame (909 points), varying with machine load. `refit_frequency_experiment.py`,
`holdout_experiment.py` and `grid_sweep_holdout.py` each compute the frame's points once
and slice them per window (frame-anchored).
`objective_experiment.py` memoizes per window instead, since every arm and every seed of a
transition re-fits the same window. All are installed by monkeypatching
`optimizer.build_calibration_inputs`. **A new harness needs one of them**, and should
print progress — a silent six-minute build looks like a hang.

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

- The figure that decides is the **base asset accumulated against buy-and-hold**, out of
  sample, on one continuous run. Euros are a denomination, not the goal.
- **"Beats buy-and-hold" is not a result on its own.** Report where a config lands in the
  distribution of the whole search space over the same span. In the 2025-04..2026-03 window
  88 of 105 configs beat hold, so clearing that bar says almost nothing.
- **An overlay that substitutes a result is not a simulation of the intervention.** Replacing
  a gated period's outcome with buy-and-hold's reported a +21.9 point prize where actually
  re-running the gated simulation delivers +0.6, because the intervention changes what the
  bot holds afterwards and everything downstream depends on that. Simulate the change; do not
  arithmetically stand in for it.
- **Any measurement that slices time must say, in writing, what happens to the open
  position at each boundary.** A restart charges an entry fee the running bot never pays and
  liquidates a position it would have kept, and it hits low-frequency configs hardest, so it
  distorts rankings and not merely levels. This has contaminated three separate measurements
  in this study — the chained walk-forward, the cadence comparison, and the first stability
  run — and each time the tell was two measurements of the same data disagreeing.
- **Write down decisions, not only defects.** This document lost two settled decisions
  (shared `stop_pct`, `train_split = 1.0`) because it recorded only open questions, and
  both were later contradicted by work done from it. "Decisions taken" is the place.
- The optimizer must simulate the bot that is deployed. Two of the six defects above were
  divergences between the simulator and production, and both reordered candidates.
- A search bound that a winning candidate touches is a bound to widen and re-measure, not
  a result to record.
- Before trusting any number, ask what it would look like if the harness were wrong. Three
  of the defects here were invisible until a second, independently written measurement
  disagreed with the first.
