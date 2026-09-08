# Optimizer Validation — Design and Study State

Status (2026-09-07): **four defects fixed, one harness defect fixed — and five avenues closed
by measurement.** No way of choosing a config has been found that carries forward, and no
modification of the strategy tested here changes that. What remains is a conditional edge with
no way to tell when it applies. See the table under "How to continue".

The last positive claim went with the resolution check. Re-running the whole grid at 1-minute
resolution — production's actual decision cadence, against the 15 minutes everything here was
measured on — leaves the rank-based negative findings intact but scatters individual configs
by ±9 points, and the `min_margin` 0.040–0.070 region does not replicate. The winners make
**3 to 7 operations in 275 days**, which is too few to tell an edge from a run of luck, and
that one fact is consistent with every negative result in this file. See "The simulator's own
time resolution moves a config by ±9 points".

**That last sentence is the one to act on, and USDCEUR is why.** Every negative result here
was measured at a sample size where an edge could not have been detected had it existed. On
USDCEUR — once `MINIMUM_CHANGE_PCT` is scaled to the pair, having been found to be an ATR
multiple disguised as a price fraction — configs make **11 to 45 operations** instead of 3 to
7. Nothing about predictiveness has been re-tested there yet, and until it is, the five closed
avenues are properties of a small sample as much as of the strategy. See "USDCEUR: the pivot
threshold is an ATR multiple wearing a price fraction".

The question this document exists to answer is unchanged — *can the optimizer produce a config
that beats buy-and-hold out of sample?* — but the answer it carried before 2026-09-02 rested
on measurements that were wrong. Everything PnL-based in the previous version of this file
(`optimizer-grid-derivation-design.md`) is retracted; see "Retracted".

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
are no longer needed for a bull-regime test — this frame contains one — but they remain the
only unused data. And the frame is not perfectly continuous: it holds
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
remainder, in euros, against hold over the same span. Produced by `holdout_experiment.py`, since deleted.
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

Produced by `refit_frequency_experiment.py` (since deleted), one continuous run per arm over a single
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
It is answered two sections down, and the answer is that it does not.

### An asymmetry worth explaining

A single fit lands at median percentile 50, i.e. chance. Yet twelve sequential re-fits
(`reajuste`) land *below the bottom quartile* — worse than chance. If each choice were
merely uninformative, repeated choices should average toward the median, not below it.

So the damage appears to be in **changing** the config, not in choosing it. Two candidate
mechanisms, untested: a config change mid-position strands the bot between an activation
barrier it was working toward and a new one, or trailing-window fits select actively bad
configs rather than random ones. Worth resolving before any reconfiguration feature ships.

### Config quality does not persist either (2026-09-05)

Produced by `config_stability.py` (since deleted): all 105 configs run once over
2025-04-01 .. 2026-03-31, that single run split into six ~60-day periods by the ratio of its compounded
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

The measurement used an `EngineConfig.no_sell_bars` mask (since removed, `ca4fc5b`)
suppressing the *sell* side on chosen bars: in an up-trend the damage is leaving the asset
and rebuying higher, so the exit is the harmful action, while a re-entry is what gets the
bot back in and must never be gated. The stop kept trailing while masked, so the mask
deferred an exit rather than cancelling it. It was always empty in production.

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

**Read this as bounding *deferred sell exits*, not as bounding a rally gate.** Suppressing
sells is inert whenever the rally opens on a cash leg, which is most of them. The stronger
intervention — forced full allocation — is bounded two sections down, and is also negative,
for a different and more interesting reason.

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

### The asymmetric stop is closed too (2026-09-06)

The last structural idea in this file: `k_stop_buy` and `k_stop_sell` are already calibrated
from separate pivot samples, and the shared-`stop_pct` decision collapsed them onto one
percentile. Widening the *sell* side should hold the position through deeper retracements, so
the bot leaves the asset less readily — the structural counterpart of a trend filter, with no
forecast in it.

Testing it needed **no production change at all**: `PairCalibration` has always carried the
two sides separately, so building the calibration in the harness is enough. Shipping it would
need one (`PairConfig` stores one `stop_pct` per level, not per side), which is a cost worth
paying only if it pays.

Paired comparison — same `min_margin`, same buy-side percentile, only the sell width differs,
excluding pairs where nothing changed:

| Gap (sell − buy) | Δ total | Δ rallies | Improved |
|---|---|---|---|
| −0.4 (buy wider) | **+0.43 %** | +0.12 % | 13/21 |
| −0.2 | +0.09 % | +0.06 % | 41/63 |
| +0.2 | −0.09 % | −0.06 % | 24/63 |
| +0.4 (sell wider) | **−0.16 %** | −0.12 % | 10/21 |

**The effect is real and runs the other way.** Monotone across all eight gap levels and in
both columns, but widening the *sell* side hurts and widening the *buy* side helps. The
mechanism proposed above does not exist. What does: a wider buy-side stop waits for a larger
bounce before rebuying, so the bot stays in cash longer through declines and rebuys lower —
which pays in a frame where nine of twelve periods are not rallies, and should invert in a
rising one. Regime, not structure.

The size settles it regardless: ±0.4 points where period-to-period variation is 38, with
13/21 pairs improving at best — 62 %, barely off a coin flip.

### Selecting by past consistency does not work either (2026-09-06)

The natural operator question — which config beats hold most often, with the best return —
answered over the twelve periods, then validated by choosing on the first six and measuring on
the last six:

| Config | Beat hold | Accumulation |
|---|---|---|
| `mm=0.050 s=0.9` | 8/12 | +52.7 % |
| `mm=0.050 s=0.7` | 8/12 | +44.5 % |
| `mm=0.040 s=0.9` | 8/12 | +38.2 % |

| Group chosen on periods 1–6 | Beat hold (median) | Accumulation (median) |
|---|---|---|
| the 10 most consistent | 3.0 / 6 | +31.7 % |
| **the whole space** | 3.0 / 6 | **+32.2 %** |

Identical, and marginally worse. That is the third selection criterion to fail, after
in-sample PnL and reconfiguration cadence.

**What survives is a weaker but usable statement.** The top ten cluster without exception at
`min_margin` 0.040–0.070, and the rest of the space is characterised: `mm` ≤ 0.010 sits at
percentile 0–2 in every period, `mm` ≥ 0.15 barely trades. So the *region* is identifiable
even though the *choice within it* is not. If a config is to be deployed, take one in
0.04–0.07 and settle `stop_pct` on operational grounds — operation count, fees, time spent out
of the asset — because past performance carries no information at that resolution.

**This is the claim the resolution check below fails to replicate.** Read the next section
before acting on it.

### The simulator's own time resolution moves a config by ±9 points (2026-09-07)

Every number above was produced on 15-minute candles, and the engine resolves the whole
trailing-stop decision once per candle. Production polls every `SLEEPING_INTERVAL` — 60 s —
so the deployed bot ratchets its trailing stop **fifteen times more often** than the thing
that was measured. The 15-minute arm is not a lower-resolution view of the bot; it is a
different bot.

There is also a genuine look-ahead inside a single bar. On a `sell` leg
`simulate_operations` raises `trailing_price` to the bar's **high**, recomputes `stop_px`
from it, and only then tests the bar's **low** against that raised stop. On a bar that fell
before it rose, the live bot still held the older, lower stop when the low happened and
would not have exited; the engine exits, and books the exit at a price that was gone.

`scripts/analysis/execution_fidelity.py` isolates resolution from everything else: ATR is
always computed on the 15-minute series and forward-filled onto the finer grid, and the
calibration schedule is built once on 15-minute bars and remapped by timestamp. So the
volatility levels, the `K_STOP` values and the stop distances are identical in all three
arms, and the only thing that varies is how often the logic is evaluated. XBTEUR,
2025-04-01..2025-12-31 (275 days, hold −2.2 %), calibration from 2025-01-01, fee 0.4 %/leg.

| | 21 configs (the middle of the space) | 105 configs (the full grid) |
|---|---|---|
| rho 15m vs 1m | +0.722 | **+0.905** |
| rho 15m vs 5m | +0.879 | +0.945 |
| rho 5m vs 1m | +0.856 | +0.923 |
| top-10 overlap 15m/1m | — | **9/10** |
| median delta 1m−15m | +0.00 pts | +0.00 pts |
| largest \|delta\| | 18.4 pts | 19.2 pts |
| sign flips | 0/21 | 3/105 |

**No bias, but real dispersion.** The median delta is 0.00 and the mean +0.09, so the
look-ahead does not systematically inflate anything. What it does is scatter: σ ≈ 9 points
per config over 275 days, with individual configs moving up to 19. `mm=0.050 s=0.7` goes
from +22.3 % (near the top of its region) to +4.5 %; `mm=0.060 s=0.5` goes from +5.5 % (the
worst of its region) to +23.9 %. Nothing changed but the clock.

**Gross rankings survive; fine ones do not.** Across the full grid rho is +0.905 and 9 of
the top 10 coincide, because the grid is dominated by structure resolution cannot touch —
the `mm = 0.00` family loses 60–80 % in every arm. Restricted to the middle of the space,
where a deployment choice is actually made, rho falls to +0.722. Both numbers are true and
they answer different questions.

**It does not converge.** If the coarse arm were an approximation of a true result,
refining 5m→1m would reshuffle far less than 15m→5m. It reshuffles the same amount
(+0.945 then +0.923; +0.879 then +0.856 on the subset). There is no true ranking being
approached — which is what one expects when the winners are decided by a handful of trades.

**The consequences, in order of importance:**

1. **The rank-based negative findings stand.** "Fitting does not predict", "quality does not
   persist", "trend filtering is dead", "the asymmetric stop is closed" are all comparative,
   and comparisons are what the resolution check shows to be stable at the gross level. The
   framework is not broken.
2. **The `min_margin` 0.040–0.070 region does not replicate.** Over 2025-04..2025-12, scored
   as total base-asset accumulation on one continuous run, the region's median is **−0.42
   points** against the rest of the space at 15m and **−2.20 points** at 1m, and it holds
   3/10 and 2/10 of the top ten where 20 of 105 configs would give ~1.9/10 by chance. The
   original claim used a different window (2025-04..2026-03) and a different statistic (rank
   consistency across twelve periods), so this is a failure to replicate rather than a clean
   refutation — but it removes the only positive result the study still had.
3. **The winners barely trade.** `mm=0.070 s=0.5` tops both arms at +27.0 % on **7
   operations in 275 days**; the grid's median config makes 3. Four configs at
   `mm = 0.17..0.20` tie at exactly 24.70 % — the quiescent corner, where the activation
   barrier is almost never crossed. A 27-point base-asset gain drawn from seven trades
   cannot separate skill from luck, and that single fact explains why nothing persists and
   nothing predicts.

**Still unmeasured: slippage.** All three arms fill at exactly `stop_px`. Production detects
the breach at a 60 s poll and then places a *limit at the market price*, which on a falling
market is below the stop. That term is strictly negative and none of these numbers contain
it. The 1-minute arm matches production's decision *cadence*, not its fill.

Caveat on the data: the 1-minute series has 1 930 gaps in the window (Kraken omits candles
with no trades), 393 215 bars against 396 000 nominal — 99.3 % coverage, mostly one- or
two-bar holes.

### USDCEUR: the pivot threshold is an ATR multiple wearing a price fraction (2026-09-07)

The second pair, chosen for the axis the strategy actually runs on: USDCEUR's `ATR/close`
median is **0.000516** against XBTEUR's **0.002288** — 4.4× quieter — which is the sharpest
contrast the archives offer, and far more informative than a correlated crypto major.

Transplanting the config unchanged does almost nothing, and the reason is a constant nobody
had questioned. `MINIMUM_CHANGE_PCT` (default 0.02) filters pivots as a fraction of **price**,
but what it means is a multiple of **ATR**, and the two only coincide on the pair it was tuned
on:

| Pair | `MINIMUM_CHANGE_PCT` | × median ATR | Structural events / side / year | Min samples per level |
|---|---|---|---|---|
| XBTEUR | 0.0200 | 8.7 | 169 | 41 |
| USDCEUR | 0.0200 | **38.8** | **10** | **7** |
| USDCEUR | 0.0045 | 8.7 | 141 | 27 |

At 0.02 a "structural" move on USDCEUR is forty ATRs, so a whole year yields ten pivots per
side and the five volatility levels are filled from the same ten episodes. The `K_STOP` values
are then not estimates but one observation repeated — median K 26.4 at LL against XBTEUR's
7.3. The ATR-equivalent threshold restores the sample almost exactly, and the K medians land
on XBTEUR's too (MV 3.98 vs 3.85, HH 2.72 vs 2.26).

Swept, at Kraken's real stablecoin fee of 0.20 %/leg (tier 1), 2025-04-01..2025-12-31, hold
−7.87 %:

| `MINIMUM_CHANGE_PCT` | × ATR | Fee/leg | Configs that trade | Best 15m | Best 1m |
|---|---|---|---|---|---|
| 0.0200 | 38.8 | 0.40 % | 12/105 | +2.90 % | +4.40 % |
| **0.0045** | **8.7** | 0.20 % | **21/105** | **+5.94 %** | **+6.76 %** |
| 0.0020 | 3.9 | 0.20 % | 22/105 | +3.50 % | +5.26 % |

Going *below* the ATR-equivalent value is worse too, so this is not "smaller is better on a
quiet pair" — the calibration has a natural scale in ATR units and both departures lose it.
The two changes decompose cleanly because `fee_rate` never enters the decision path in
`trading/engine.py` (only `_leg_pct`, `_record_stop_exit` and the opening buy): the threshold
alone explains 12 → 21 trading configs, the fee only shifts levels.

**The find worth acting on is the operation count.** With the threshold and the `min_margin`
ceiling both scaled to the pair, USDCEUR trades **5 to 273 times** over the window where
XBTEUR managed 3 to 7:

| Config | ops 15m / 5m / 1m | USDC 15m / 5m / 1m |
|---|---|---|
| `mm=0.0000 s=0.5` | 270 / 268 / 273 | −42.9 % / −42.8 % / −43.1 % |
| `mm=0.0030 s=0.7` | 31 / 35 / 45 | +1.1 % / −0.7 % / +0.2 % |
| `mm=0.0045 s=0.9` | 13 / 15 / 17 | +4.5 % / +3.2 % / +4.1 % |
| **`mm=0.0053 s=0.9`** | **11 / 11 / 13** | **+5.5 % / +5.4 % / +6.3 %** |
| `mm=0.0075 s=0.9` | 5 / 5 / 5 | +3.3 % / +3.3 % / +3.3 % |

This is the first setting in the study with enough operations for a predictiveness test to
have any power, and the optimum is **not** at the quiet end — 5 operations scores +3.3 % and
11 scores +6.3 %, which is what a mechanism looks like rather than a slow convergence on hold.
The high-frequency end fails for the obvious reason: 270 operations at 0.4 % round trip is
54 points of fees.

The resolution result reproduces on this pair, in shape and not only in sign: median delta
−0.16 points, largest 5.5 on returns near 5 %, top-ten overlap 8–9/10, rho +0.80 to +0.92
between arms, and again no convergence.

**Two things this is not.** The base asset here is USDC, so "accumulating the base asset"
means accumulating dollars — a EUR/USD carry position, not the same goal as accumulating
bitcoin. In euros the best config ends at **−2.1 %** against **−7.9 %** for holding USDC,
which is a hedge, not a profit. And the USDCEUR series has an 11-day hole (1 077 missing
15-minute candles, 3 229 at 5 min), so any window overlapping it is thinner than it looks.

**Production consequence, unimplemented.** `MINIMUM_CHANGE_PCT` is a single global in
`trading/market_analyzer.py`, read at call time by `detect_pivots`. Making it per-pair is the
shallow fix; expressing it as a multiple of the pair's median `ATR/close` is the correct one,
since that is what it already means and it would then calibrate itself for any pair. Neither
is done — everything above is a monkeypatch in the harness.

### High volatility is not more directional (2026-09-07)

The last untested lever, and the only one that was not a repeat: direction cannot be
predicted, but volatility **can** — realized volatility is strongly autocorrelated where
direction is not, and the bot already classifies it every tick as LL…HH. So unlike the
trend filter, whose oracle was worth +0.6 points and whose realizable fraction was near
zero anyway, a volatility gate would capture a real share of whatever premium exists. The
proposal: trade the low-volatility chop, stand aside through the high-volatility episodes.

**But in base-asset terms the enemy is not volatility, it is sustained direction.** A
violent oscillation that returns to its starting price is the best case a trailing stop
can have — maximum path, no displacement. What loses base asset is selling and not getting
back in below. So the proposal carries a hidden empirical claim, and everything rests on it:

> in BTC, high-ATR stretches are disproportionately **trending** rather than **oscillating**

That is measurable directly from the price series — no engine, no configs, no fees. For a
window of N bars, the Kaufman efficiency ratio `|net| / Σ|steps|` is 1 for a pure trend and
0 for a pure oscillation, and it is dimensionless, so it compares across volatility levels
where a raw return could not. `scripts/analysis/volatility_regime_screen.py`, XBTEUR
15-minute candles over the range the archive actually covers (2025-01-01 .. 2025-12-31),
**non-overlapping** windows so the reported n is the real n, each window classified by the
ATR level of its own first bar.

A driftless random walk does not score 0 — it scores `1/√N` — so the table reports
**ER/null**, and the null is what a coin flip gives:

| Horizon | LL | LV | MV | HV | HH | n per level |
|---|---|---|---|---|---|---|
| 1 h | 1.01 | 0.99 | 0.99 | 1.00 | 0.98 | 435–2 625 |
| 4 h | 1.05 | 0.96 | 0.95 | 0.98 | 1.03 | 113–673 |
| 12 h | 1.01 | 0.99 | 1.06 | 0.98 | 0.98 | 43–217 |
| 24 h | 1.01 | 0.89 | 0.98 | 0.89 | 1.11 | 17–118 |

**Flat, at every horizon, with no monotone rise from LL to HH.** The 72-hour row is omitted
because its levels carry 5–38 windows. USDCEUR shows the same flatness. High-ATR stretches
are exactly as directional as low-ATR ones, which is to say not directional at all.

**The metric is not broken — it was checked against synthetic series before the null was
believed**, which the Choppiness episode in this document is the reason for: a pure trend
scores ER 1.000, a pure sawtooth 0.000, a trend buried in noise 1.08 (N=16) rising to 1.41
(N=96), and a random walk 1.07–1.11. Full dynamic range. Against that empirical baseline of
~1.07, BTC's pooled 0.95–1.04 is if anything *marginally less* efficient than a coin flip.

That last point is larger than the question that prompted it: **over 1 h to 3 d, BTC's price
path is statistically indistinguishable from a driftless random walk at every volatility
level.** There is no trend structure at these horizons for any filter to find.

**One pattern was not acted on, deliberately.** Mean signed return rises with horizon for HH
(+0.03 / +0.15 / +0.79 / +1.96 %) and falls for HV (−0.02 / −0.05 / −0.26 / −0.58 %), which
would be the worst case for the bot if real — the top volatility band skewed *up* is exactly
the rally it cannot survive. It is not actionable: n is 17–43 at the horizons where the
effect appears, roughly 2.5 standard errors, and the two adjacent bands carry **opposite**
signs, which is not what a genuine volatility-direction link looks like. Recorded so it is
not rediscovered as news.

**What this closes:** gating on volatility, and with it the stated rationale for
differentiating `stop_pct` by level (wide in HV/HH so the position rides the episode, normal
in LL/LV/MV). **What it does not close:** per-level stops as a tuning question — the 105-config
sweep uses `dict.fromkeys(LEVELS, stop)`, one shared value, so per-level differentiation is
genuinely unexplored. There is simply no hypothesis left proposing it, and the study's record
on searching a space with no mechanism behind it is five for five.

### The rally loss and the crash gain are the same position (2026-09-07)

The `+0.6` above bounds a weaker intervention than its name suggests, and reading it as the
ceiling on *avoiding rallies* was wrong. It suppressed only **sells**. The loss a rally
inflicts has two halves — selling into the rise, which that mask covered, and sitting in cash
while the price rises and having to rebuy higher, which it could not cover, because the
re-entry was deliberately excluded from the gate. Whenever the rally opened on a **cash leg**
the mask was inert: no sell to suppress, and the bot ate the rise from outside exactly as it
would have ungated. The deleted script's own docstring said so and it was read as a footnote.

So `+0.6` bounds "defer sell exits during a rally". `EngineConfig.force_hold_bars` and
`scripts/analysis/rally_gate_oracle.py` bound the intervention that was meant: on a masked
bar the bot must be **fully in the base asset** — the sell is suppressed with the stop still
trailing, and a bot holding cash is **forced in** at that bar's price, paying the entry fee.
Within this bot's action space (hold, or flip) there is no stronger response to a detected
rally, which is what makes it a ceiling. XBTEUR 2025-04-01 .. 2025-12-31, 105 configs, one
continuous run per arm, 60-day periods, a period gated when hold rose above +5 % (2 of 5
periods, 44 % of bars):

| Arm | Median | Best | Worst | Beat hold | Ops |
|---|---|---|---|---|---|
| ungated | **+18.8 %** | +27.0 % | −79.5 % | 88/105 | 14.6 |
| oracle (forced allocation) | **+15.5 %** | +38.4 % | −55.0 % | 96/105 | 9.3 |

**A perfect rally detector, given the strongest response this bot has, costs 3.2 points of
median.** But the per-period breakdown is the finding, not the total:

| Period | hold | ungated | oracle | delta |
|---|---|---|---|---|
| 2025-04-01..05-30 **(gated)** | +20.4 % | −7.5 % | **−0.4 %** | **+7.1** |
| 2025-05-31..07-29 **(gated)** | +11.3 % | −10.2 % | **−0.0 %** | **+10.2** |
| 2025-07-30..09-28 | −8.2 % | +9.0 % | +9.3 % | +0.3 |
| 2025-09-28..11-27 | −15.5 % | **+27.7 %** | **+6.3 %** | **−21.4** |
| 2025-11-27..12-31 | −5.9 % | +0.0 % | +0.0 % | −0.0 |

**Inside the gate it works perfectly.** The two rally periods go from −7.5 % and −10.2 % to
zero — holding the asset *is* zero in base-asset terms, and the gate recovers essentially the
whole loss, +17.3 points. The premise that the rally is where the bot bleeds is confirmed
exactly.

**Then it gives back −21.4 in the crash it never touched**, two months after the mask lifted.
The ungated bot earns +27.7 % in the September–November fall; the gated one manages +6.3 %,
having arrived there frozen for four months, out of position, with its activation barrier and
trailing stop anchored to a price the other arm never saw. Trade count falls 14.6 → 9.3.

**The mechanism, and it is the deepest result in this file.** This strategy is a
mean-reversion harvester: it is permanently positioned to sell into strength and buy into
weakness. In a rally that positioning costs base asset. In a crash the *same* positioning
earns it. They are not two behaviours, one good and one bad — they are one behaviour seen in
two regimes, and the gate cannot disarm the losing half without disarming the winning half
too. **The rally loss is not a defect a filter can remove; it is the price of the position
that earns in the fall.**

That also explains the standing puzzle of the "conditional edge with no way to tell which is
coming". The condition is not something the bot could exploit even with perfect foresight,
because foresight only lets it stop trading, and stopping trading is what costs it the fall.

The tails move a great deal — worst −79.5 % → −55.0 %, best +27.0 % → +38.4 %, share beating
hold 88 → 96 — but that is variance reduction on one span, the same pattern the weaker oracle
showed, and "best of 105" is the statistic this document has already shown does not predict.

**Scope.** One window, two rally periods, one pair. Per-period medians are medians across
configs, so they do not compound to the total (the median config differs per period);
compounding them gives +15.6 % and +15.7 % against the reported +18.8 % and +15.5 %, which is
the expected size of that gap. A finer gate — blocking only the sharply trending sub-stretches
rather than whole 60-day periods — is untested, but the mechanism argues against it: the cost
is being out of position when the gate lifts, and every gate lifts.

### Free per-level stops do not converge (2026-09-08)

The one region the study's sweeps never entered is five independently searched `stop_pcts`
— every sweep uses `dict.fromkeys(LEVELS, stop)` — and the claim that it holds profitable
high-operation configs came from the optimizer *before* this branch, i.e. scored with the
cash leg paid as if the bot held a short (worth up to +162 points, and most on exactly the
configs that spend the most time in cash) and with a single calibration. So the deployed
search was re-run on the fixed engine through `scripts/analysis/run_optimizer_csv.py`:
XBTEUR 2025, `AUTO`, fee 0.4 %, `train_split` 0.67, `stop_pcts` 0.0–1.0 step 0.1 on all five
levels, `min_margin` fixed at 0.004, `k_act` off.

**`converged: False` — 0 of 4 seeds agreed on a config after escalating to 3 000 trials each**
(12 000 evaluations, 4 832 s). AUTO judges convergence on the config, and four TPE searches
returned four different answers. That is the identifiability problem behind the shared-stop
decision, now measured rather than argued.

Two more things the run settled. Operation counts did rise (47–53 train, 8–12 test, against
3–7 for the shared-stop sweeps) — but that is `min_margin = 0.004`, a barrier ten times
narrower than the 0.04–0.07 region, not the free stops: given 0.0–1.0 the search fled to the
*top* of the grid (LV/MV/HV/HH at 0.9–1.0), the widest stops it could find, which is the fee
pushing back. And every top-5 candidate is negative in euros (best `robust_pnl` −3.27 %).

In base asset the same top candidate reads differently, and that is the last finding:

| | bot (EUR) | hold (EUR) | base asset |
|---|---|---|---|
| 2025 | −5.19 % | −17.41 % | **+14.79 %** |
| train (Jan–Sep) | −1.98 % | +5.21 % | −6.84 % |
| test (Sep–Dec) | −3.27 % | −21.48 % | **+23.19 %** |

The two halves are in opposite regimes, and on the test half the ranking metric and the
objective disagree in *sign*. See defect 6 for what that does and does not imply.

**Decision:** the current optimizer is closed as a research tool, and the shared-stop decision
stands — and since a shared stop makes the space enumerable, the sampler went with it; see
[`optimizer-simplification-design.md`](optimizer-simplification-design.md). The cost of the run that reached this: the first attempt lost its result to a
formatting error after 80 minutes, which is why the runner now writes to disk first.

### Per-side activation is closed too (2026-09-08)

The last structural lever, and the only one whose rationale came from the base-asset
objective instead of from guessing the regime: being out during a rise loses coins for good,
being in during a fall costs none, so the sell side carries the real risk and the buy side
only opportunity cost. That argues, statically, for a wide activation barrier to sell and a
narrow one to rebuy. Per-side `k_act`/`min_margin` were removed from production for "no
observable benefit", but on the buggy engine; `EngineConfig.min_margin_sell` /
`min_margin_buy` (overrides of the shared value, `None` in production) re-measure it on the
fixed one. `scripts/analysis/side_margin_sweep.py`: for each of the 105 symmetric configs,
its asymmetric neighbours at δ in both directions, one continuous run each, XBTEUR
2025-04-01 .. 2025-12-31, delta of base-asset accumulation against the symmetric config:

| δ | direction | median | mean | p25 | p75 | share > 0 | ops |
|---|---|---|---|---|---|---|---|
| 0.01 | sell reluctant / buy eager | +0.00 | −1.54 | −6.25 | +0.97 | 30 % | 8.4 |
| 0.01 | sell eager / buy reluctant | +0.00 | +1.92 | −1.89 | +4.28 | 36 % | 7.2 |
| 0.02 | sell reluctant / buy eager | **−5.47** | −3.63 | −11.30 | +0.15 | 26 % | 5.1 |
| 0.02 | sell eager / buy reluctant | +0.00 | +1.57 | −3.87 | +4.05 | 39 % | 3.7 |
| 0.04 | sell reluctant / buy eager | **−6.99** | −5.68 | −12.41 | −5.35 | 14 % | 3.8 |
| 0.04 | sell eager / buy reluctant | −5.81 | −1.78 | −8.75 | −1.50 | 18 % | 1.9 |

**The hypothesis direction is the worse one, and it gets worse with δ** — median 0.00 →
−5.47 → −6.99, share improving 30 % → 26 % → 14 %. The opposite direction sits at zero. The
same sign as the asymmetric-stop result, for presumably the same reason: a side made
reluctant also acts later and worse when the market moves the way that side profits from,
and the asymmetry cancels. Operation counts fall under any asymmetry (14.6 → 8.4/7.2 →
3.8/1.9), so what a per-side margin mostly does is trade less. Best-of-arm is flat (+27.0 %
symmetric against +20.6 … +27.3 %), and best-of-105 does not predict anyway.

This was declared the last avenue before it was run. It is.

### Switching the config by regime is closed (2026-09-08)

The proposal, after the per-side result: since the bot cannot tell direction, let the
*operator* declare the regime — lateral, falling, rising — and run one config per regime
(a lateral config that trades at least 4–5 times a month, a falling config, and hold or
nothing in a rally), switched through `PATCH /config`. The question is not whether such
configs exist per segment (with hindsight one always does — that is the percentile-50
result) but whether switching them, even with *perfect* regime labels, beats one fixed
config. If it does not, no detector and no operator can recover it.

**Labels by shape, not by clock.** BTC's 2025 moves are impulses of days between weeks of
range, so fixed windows mix the two. A box-containment rule (the longest stretch fitting in
a 10 % box) was tried first and absorbed the impulses — they were one- or two-day jumps
between adjacent boxes, and 97 % of days came out lateral. The rule that respects the shape
detects the movement first: a day is part of an **impulse** when it lies inside any pair of
daily closes ≤ K days apart with |net| ≥ M (direction by the impulse's net sign); a gap
between impulses of ≥ D days is **lateral**, shorter gaps are **short** (a pause, reported
and not excluded). `M = 10 %`, `K = 7 d`, `D = 7 d`, fixed before any config was ranked;
control at `M = 7 %`, `K = 5 d`. Primary labels on 2025-04-01 .. 2025-12-31 (hold −2.24 %):

| Class | Segments | Days | Median length | Median \|hold\| |
|---|---|---|---|---|
| lateral | 6 | 207 (75 %) | 39 d | 2.0 % |
| falling | 3 | 28 (10 %) | 8 d | 12.7 % |
| rising | 4 | 35 (13 %) | 8 d | 11.2 % |
| short | 2 | 5 (2 %) | 4 d | 3.5 % |

**Method.** `scripts/analysis/regime_switch_oracle.py`. Every one of the 105 configs runs
once, continuously, and is sliced at the segment boundaries by marking to market (quotient
of growth factors), so the per-class ranking sees the state each config carried into the
segment. The class winner is the config with the best compounded base-asset accumulation
over that class's segments. The switched arm is then **one** continuous run whose
`min_margin` and `stop_pct` change at each boundary — the retired `activation_schedule`
reproduced without touching the engine: the switched run's calibration-schedule entries
carry their `min_margin` (a `PairCalibration` subclass) and `activation_distance` is
patched in the script to read it. A second switched arm also forces full allocation through
the rising segments (`force_hold_bars`), the strongest thing the bot can do with a known
rally.

**Per class** (primary labels; "all" = beats hold in every segment of the class):

| Class | Configs beating hold in all | Best config | Compounded | Ops/month | Best with ≥ 4 ops/month |
|---|---|---|---|---|---|
| lateral | 0/105 (best: 4/6) | `mm=0.07 s=0.5` | +27.0 % | 0.6 | `mm=0.01 s=0.7` +6.8 %, 4/6, 5.9/mo |
| falling | 95/105 | `mm=0.04 s=0.7` | +50.3 % | 3.2 | `mm=0.03 s=0.7` +36.7 %, 4.3/mo |
| rising | 0/105 | `mm=0.20 s=0.9` | −18.8 % | 0.9 | `mm=0.01 s=0.9` −24.9 % |

**Switched against fixed** (base asset over the window, hold = 0 %):

| Arm | Primary (M=10, K=7) | Control (M=7, K=5) | Ops |
|---|---|---|---|
| median of the 105 fixed | +18.8 % | +18.8 % | |
| best fixed (`mm=0.07 s=0.5`, chosen in-sample) | +27.0 % | +27.0 % | 6 |
| recommended fixed (`mm=0.05 s=0.9`) | +20.9 % | +20.9 % | 6 |
| **switched, perfect labels** | **+7.6 %** | **+8.9 %** | 4 |
| switched + full allocation through rallies | +12.8 % | +10.8 % | 10–14 |

The oracle is 18–19 points *below* the best fixed config and below the median of the
105 — at both labelings, so it is not the thresholds.

**Why, and this is the mechanism the whole idea runs into.** Look at the operation counts:
the class winners make 0–1 operations per segment. A falling segment scores +14.6 % or
+24.6 % with **zero** operations — the bot was already in cash when the fall began; a rising
segment scores −10.1 % with zero operations — the bot was already in cash when the rise
began. What a regime returns is decided by the side the bot holds when the regime starts,
and that was fixed by the previous regime's path, not by the config now in force. The
config cannot change the side either, because activation needs a *favourable* move first:
a "falling" config cannot make a bot that holds the asset sell into a fall (the sell never
activates), and a "rising" config cannot make a bot in cash buy into a rise. The forced
arm shows the other half: buying in at each rally start does recover the rallies (−10.1 →
−0.6 / −0.3 / −0.0) and then gives it back in the November fall (+24.6 → +0.0, zero ops: in
the asset, sell never activated) and the December range (+14.2 → −0.0). It is the same
divergence the rally gate found, now with the config free to change and the labels perfect.

**On the operation-count requirement.** No class winner exceeds 1.1 operations a month.
Over the whole window 8 of the 105 configs reach 4 a month; the best of them,
`mm=0.01 s=0.6`, accumulates **−20.7 %** with 60 operations — at 0.4 % a trade, 4–5
operations a month is 19–24 % a year of fees, and nothing in the grid earns that back.
"Trades at least 4–5 times a month" is a fee floor, not an objective.

This closes the eleventh avenue, and the first one framed on the operator side of the bot.

### Where the loss of operating comes from (2026-09-08)

The regime result left the bot's owner with the right diagnosis: the more a config trades,
the worse it does, and the best fixed config (6 operations) simply sold and rebought at
good moments. That is a statement about the strategy, not the search, so this measures the
strategy at the level of the single cycle. `scripts/analysis/cycle_decomposition.py`
pairs every sell with the buy that follows it, across the 105 configs on
2025-04-01 .. 2025-12-31, and scores each cycle in base asset with both fees
(`P_sell / P_buy × (1 − f)² − 1`; a cycle costs −0.80 %).

**The arithmetic first.** In base asset the in-asset leg is worth 0 by construction; only
the cash leg (sell, then rebuy) changes how many coins there are. The buy cannot activate
until the price has fallen `K·ATR + mm·P_sell` below the sell, and it executes `K·ATR`
above the low — so a *clean* cycle rebuys at most at `P_sell · (1 − mm)`, and gains at
least `mm − 0.8 %`. It cannot lose. The only way to rebuy higher is the re-anchor of the
buy activation (the mirror of `reanchor_activation_price`): as soon as the price exceeds
`P_sell`, the activation follows it up, and the rebuy comes after a `K·ATR + mm` dip from
the new high. It exists so a bot in cash re-enters after a rally instead of staying out
forever, and in euros it costs nothing — cash is cash. In base asset it costs the whole
excursion. So the cash leg has a **take-profit** (the trailing buy closes it at the first
`K·ATR` bounce) and **no stop-loss** (the re-anchor lets the excursion run): the bot was
designed in euros, where the cash leg carries no risk, and in the base-asset objective the
cash leg is the *only* leg that carries any.

**Measured** (five configs per `min_margin`, `stop_pct` 0.5 .. 0.9):

| `mm` | floor | cycles | win | median win | Σ win | lose | median loss | Σ loss | worst | time in cash |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.00 | −0.8 % | 466 | 132 | +1.2 % | +195 % | 334 | −1.3 % | −660 % | −10.8 % | 54 % |
| 0.01 | +0.2 % | 123 | 79 | +0.9 % | +105 % | 44 | −4.7 % | −220 % | −13.8 % | 59 % |
| 0.02 | +1.2 % | 49 | 37 | +1.8 % | +86 % | 12 | −3.9 % | −119 % | −24.5 % | 76 % |
| 0.03 | +2.2 % | 10 | 8 | +3.2 % | +30 % | 2 | −5.5 % | −11 % | −5.8 % | 85 % |
| 0.04 … 0.20 | +3.2 … +19.2 % | 5–13 | all | +5.9 … +25.2 % | | **0** | | | | 71–88 % |

Read across a row. At `mm = 0.01` the wins sit on the floor (median +0.9 % against a floor
of +0.2 %) and the losses are five times larger (median −4.7 %, worst −13.8 %); the sum
of the losses is twice the sum of the wins. Break-even on that asymmetry needs a win rate
of about 80 %; the measured one is 64 %. At `mm = 0.02` it is 76 % measured against 81 %
needed, and one cycle costs −24.5 %. At `mm = 0.00` the floor itself is the fee, so 72 %
of cycles lose. This is the whole "more operations, worse result" effect: each clean cycle
earns roughly `mm`, each re-anchored one loses roughly the excursion, and the ratio between
the two is set by the market, not by the config.

From `mm = 0.04` up there is **not one losing cycle**, and that is the other half of the
owner's diagnosis: those configs make one to three cycles, spend 71–88 % of the window in
cash, and their result is a single sell in April rebought in November after the fall —
+24 % on that one cycle. The loss of sitting in cash through a +30 % rally is real but never
becomes a cycle, because the rebuy only came once the price was back below the sell.

**What this bounds.** With no directional signal — and every measurement here says there is
none at 1 h–3 d — the expected base-asset return of a cash leg is the variance realised
during it minus the drift during it (`E[P_sell / P_T] = exp((σ² − μ)τ)` for a driftless
walk), and no entry or exit rule changes that expectation: a rule only chooses *when* the
bot is in cash. Capping the cash-leg loss (a rebuy above `P_sell`) turns rare large losses
into frequent small ones plus fees — a change of variance, not of sign — and removing the
re-anchor is a bet that the price returns, unbounded in the other direction. Neither makes
an operation profitable; both are directional bets in disguise. The one signal-free lever
the arithmetic leaves is *where* the cash time falls: variance clusters and is forecastable
where direction is not, and a cash leg earns `σ²τ` — which is also why "avoid high
volatility" was the wrong instinct in base asset: a violent oscillation that returns to its
start is the best thing that can happen to a bot in cash. Its ceiling is `σ² × time in
cash`, about 16 % a year at σ = 40 % if the bot were always in cash, before fees and before
the drift term that a rising year dwarfs. That is not an operation-level edge and it will
not make 4–5 trades a month pay; it makes the cash time better placed. Whether it is worth
even that needs `σ² − μ` measured per volatility level, which is a descriptive run of the
regime screen, not a simulation.

### Removing the re-anchor is closed (2026-09-08)

The cycle decomposition put the whole loss of operating in one mechanism, so the owner
asked the natural question: take the re-anchor out and see whether the bot is viable. The
counter-mechanism was stated before running it — a bot in cash whose price does not come
back never rebuys, which in base asset is an unbounded loss that never shows up as a
cycle — so the ablation runs on two windows chosen in advance: the study's own
(2025-04-01 .. 2025-12-31, the price *did* come back, the most favourable window the bet
can have) and 2024-10-01 .. 2025-03-31 (60k to 100k and no return, the unfavourable one).
`EngineConfig.reanchor_sell` / `reanchor_buy` (inert, both `True` in production) switch the
activation's following of a price that runs away, per side, and `reanchor_cap_at_entry`
(inert, `False`) keeps a re-anchored activation from crossing the leg's entry price — the
owner's follow-up: re-anchor, but only up to the sell, so the rebuy comes when the price
*returns* to the level it left (`K·ATR` above it) rather than after a further `mm + K·ATR`
dip. `scripts/analysis/reanchor_ablation.py` runs the 105 configs under four arms.

| Window | Arm | median | p25 | p75 | best | worst | beats hold | ops | in cash | ends in cash |
|---|---|---|---|---|---|---|---|---|---|---|
| 2025-04..12 (hold −2.2 %) | production | +18.8 % | +14.4 | +23.5 | +27.0 | **−79.5** | 88/105 | 14.6 | 75 % | 22 |
| | no buy re-anchor | +18.8 % | +14.4 | +23.5 | +27.0 | −6.3 | 95/105 | 3.6 | 80 % | 34 |
| | no re-anchor, both sides | +18.8 % | +14.4 | +23.5 | +27.0 | −1.4 | 104/105 | 4.1 | 78 % | 24 |
| | re-anchor capped at the sell | +18.8 % | +14.4 | +23.5 | +27.0 | −2.3 | 103/105 | 4.5 | 78 % | 21 |
| 2024-10..2025-03 (hold +34.5 %) | production | −15.3 % | −20.8 | −13.0 | −4.1 | −78.9 | 0/105 | 15.5 | 78 % | 79 |
| | no buy re-anchor | −18.2 % | −21.2 | −13.0 | −9.2 | −25.7 | 0/105 | 1.5 | 88 % | **105** |
| | no re-anchor, both sides | −18.2 % | −21.2 | −13.0 | −9.2 | −25.8 | 0/105 | 1.3 | 88 % | **105** |
| | re-anchor capped at the sell | −18.2 % | −21.2 | −13.0 | −9.2 | −26.2 | 0/105 | 1.4 | 88 % | **105** |

The medians do not move because the re-anchor never fired a rebuy above `mm = 0.03` in
either window — those configs are identical under every arm, which is the cycle table
again. Where it does act, at `mm ≤ 0.02`, it does exactly what the arithmetic says:

| `mm` | production | no buy re-anchor | both off | capped | | production | no buy re-anchor | both off | capped |
|---|---|---|---|---|---|---|---|---|---|
| | *2025-04..12* | | | | | *2024-10..2025-03* | | | |
| 0.00 | −55.6 % (187 ops) | −4.5 % (13) | +8.6 % (17) | +2.0 % (25) | | −54.9 % (189) | −24.8 % (8) | −24.8 % (5) | −25.7 % (7) |
| 0.01 | −26.5 % (49) | −0.4 % (7) | +8.7 % (10) | +6.5 % (12) | | −32.0 % (61) | −23.6 % (3) | −23.6 % (3) | −23.6 % (3) |
| 0.02 | −9.9 % (20) | +0.1 % (5) | +12.1 % (8) | +5.3 % (6) | | −27.5 % (24) | −22.3 % (2) | −22.3 % (2) | −22.3 % (2) |
| 0.03 | +4.8 % (5) | +4.8 % (5) | +6.8 % (4) | +6.8 % (4) | | −17.5 % (17) | −22.0 % (1) | −22.0 % (1) | −22.0 % (1) |
| 0.04 | +17.2 % (5) | +17.2 % (5) | +17.2 % (5) | +17.2 % (5) | | −14.9 % (13) | −22.0 % (1) | −22.0 % (1) | −22.0 % (1) |

Three things, in order of weight.

1. **The mechanism is confirmed, and it is not an edge.** Without the re-anchor the active
   configs stop losing — worst case −79.5 % becomes −1.4 %, `mm = 0.00` goes from −55.6 %
   to +8.6 % — because a clean cycle cannot lose. But +9 to +12 % on 8–17 operations is
   still below the +18.8 % median of a grid whose typical member made two operations and
   sat in cash from April to November. Operating cleanly beats operating badly; it does not
   beat not operating.
2. **On the unfavourable window it is the directional bet, measured.** 105 of 105 configs
   end the window in cash, having sold once (1.0–1.5 operations) and never rebought, and
   every one of them lands at −22 % to −25 %: the sell price divided by a price that went on
   rising. The production re-anchor is a *bad* stop-loss on the cash leg — it chases — but
   it is the only one: with it, `mm = 0.03–0.05` loses −15 to −19 % instead of −22 %.
3. **The whole result is the sign of one quantity.** Whether the price comes back below
   the last sell. Yes → the active configs make +9 to +12 %; no → −22 % and the bot is out
   of the asset indefinitely. That is not a strategy property that can be tuned, it is the
   direction of the market over the window, which is where every avenue in this file ends.

**The capped re-anchor is the no-re-anchor arm with a slightly earlier re-entry**, and on
the unfavourable window it is identical to it for a reason worth having in writing: the
sells there happened in *October 2024*, at €60–63k, on the first retrace before the rally —
not high on the way down. The price never came back to €60k (the March low was ~€70k), so
no rule that waits for the price to return, at the sell level or below it, ever rebuys;
production's chase rebought at €80k in March and is the least bad of the four for it.
Where the bot sells is the first retrace after entry, and in a rising market that is near
the bottom by construction.

**From what fee would a bounded cash leg pay in a range?** The owner's follow-up, measured
on the favourable window with `--fee`, capped arm, active configs (`mm ≤ 0.02`, 6–25
operations) against the grid median and the passive configs:

| fee per trade | production `mm=0.00` | capped `mm=0.00` | capped `mm=0.01` | capped `mm=0.02` | grid median | `mm=0.05` |
|---|---|---|---|---|---|---|
| 0.40 % | −55.6 % | +2.0 % | +6.5 % | +5.3 % | +18.8 % | +20.9 % |
| 0.26 % (taker) | −43.8 % | +6.0 % | +7.7 % | +6.0 % | +19.5 % | +22.1 % |
| 0.16 % (maker) | −33.9 % | +8.7 % | +8.8 % | +6.5 % | +20.0 % | +22.9 % |
| 0.00 % | −17.8 % | +13.1 % | +10.9 % | +7.4 % | +20.8 % | +24.3 % |

At **zero** fee, on the window with the largest realised swing in the data, the bounded
cash leg operating three times a month earns +13 %; sitting in cash from April to November
earns +24 %. No fee level makes operating beat not operating, because the fee is not what
the operating configs lose to — it is the chase (production at 0 % is still −17.8 %) and,
once the chase is removed, the size of the harvest: the typical gain of a cash leg is
`½σ²τ`, about 0.33 % a month at σ = 40 % with half the time in cash, which at Kraken's
maker rate (0.16 % a side) pays for roughly one cycle a month and at four or five a month
would need ~0.04 % a side. The stop bounds the loss per cycle; it does not raise the
harvest.

Removing the re-anchor turns a bot that loses when it operates into a bot that sells once
and waits. The switches stay in the engine as inert, tested fields, like the per-side
margins; production keeps both sides re-anchoring.

### Reverse-engineering the strategy from ideal trades is closed (2026-09-08)

The owner's proposal after the per-cycle result: label a year with the moments the bot
*should* have bought and sold, then design the operating rules to replicate them. It is a
real method (label-then-learn), and it decomposes into a free step and a hard one. Labelling
is free — with the future in hand the perfect label always exists. Replicating is not a
design problem but a **prediction** problem: it needs a function of what is visible at `t`
that predicts the label. `scripts/analysis/signal_screen.py` measures that second step and
only that, with no strategy built: 15-minute XBTEUR, fit on 2021-01-01 .. 2024-12-31,
measured on 2025, samples strided at the median bars-to-next-pivot (48) so no two share an
episode, null by **circular shift** of the labels (a plain shuffle destroys their
autocorrelation and gives a null band far too narrow).

Features are strictly causal, in two families: price/volatility (returns at five lags,
ATR/close, signed Kaufman ER, distance to moving averages, RSI) as a **control**, since the
regime screen already measured that family null; and **flow** — z-scores of volume, trade
count and mean trade size, plus signed volume imbalance. Flow is the one class of feature
this study had never touched: `volume` and `count` are columns of the Kraken CSVs that no
earlier experiment read.

**The first run looked like a discovery, and it was a methodological trap worth recording.**
Against the pivot label — +1 when the next pivot is above the close, i.e. "the bot should
have been in the asset" — the out-of-sample AUCs were 0.68–0.73 for single features and
**0.767** for a logistic on the price family. That is the control family, which the script
itself says should come out null; a control that fires means the method is wrong, not that
a signal was found. Three measurements together resolve it:

| | |
|---|---|
| 1. What the label is worth | The pivot label, traded with perfect foresight at Kraken's maker fee: **+406 %** base asset over 2025 in 229 side changes. The label is not a weak target — it is a fortune. |
| 2. AUC against the honest label | Sign of the forward return at a *fixed* horizon. Every one of the 16 features, at 12 h / 1 d / 3 d: **0.43–0.52**. Nothing. `ret_4h`, which scored 0.722 against the pivot label, scores 0.499 / 0.494 / 0.482 here. |
| 3. The money test | The AUC-0.756 model's prediction used as the allocation, revised at every sample: **+10.6 %** at *zero* fee, **−33.1 %** at maker. The price family alone: +19.6 % at zero fee, −26.7 % at maker. |

**Why the pivot label leaks.** Pivots alternate min/max, so a bar in the middle of an
up-leg has a positive trailing return *and* label +1 — for the same reason, that the leg has
already begun. The classifier is not predicting the future, it is reading the present, and
the present is already in the price. Against a label that cannot be reached this way, every
feature collapses to the null. The value of the label sits entirely in *when the leg ends*,
which is exactly the part no feature predicts: a model that classifies the label at 0.756
captures 10.6 of the 406 points available, and only if trading is free.

**Flow specifically.** `vol_z`, `cnt_z` and `trade_size_z` are inside the null even against
the tautological label (AUC 0.465–0.505, p 0.24–0.85); only the signed imbalances rise with
it, exactly as the price features do and for the same reason. On the honest label, flow is
0.44–0.52 like everything else, and a flow-only model loses money at zero fee (−8.3 %). The
one untested feature class in the data is tested, and it is null.

This closes the reverse-engineering avenue, and it closes it more broadly than the others:
the result is a property of the market at this resolution, not of the bot, so it applies to
any strategy built on these data — grid, trailing, or otherwise. What would reopen it is
data this study does not have (order book, trades tape, cross-asset, funding), not a better
rule over the same OHLCV.

### Still not established

- **Whether any data this study does not hold predicts.** Order book, trades tape, funding
  rates, cross-asset. The signal screen tested everything in the OHLCV archives, including the
  volume and trade-count columns nothing else had read, and found the null. That bounds these
  data, not all data.
- **Whether anything predicts on a pair that trades enough.** Every predictiveness and
  persistence result in this file was measured on XBTEUR, where the winners make 3–7
  operations. USDCEUR with a pair-scaled threshold makes 11–45, which is the first setting
  where the test could have power. **This is now the most informative thing left to run.**
- **Whether the strategy can beat hold at all**, given that it needs a forward regime
  signal and the only candidate tested has none. See "The rally is the problem".
- **What a non-zero `hodl_pct` would do.** Production can hold a fraction of the target
  allocation permanently; the engine cannot model it (see the divergence below), so nothing
  here measures the one lever that would soften a rally without predicting it.
- **What slippage costs.** Every arm of every measurement here fills at exactly `stop_px`.
  Production places a limit at the market price after detecting the breach, and chases it
  with `reprice_closing_order`. The term is strictly negative and entirely unmeasured; at 3–7
  operations per run it is small in aggregate, but so is everything else being compared.

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
- **The current optimizer is closed as a research tool, and free per-level stops with it.** The deployed AUTO search on the fixed engine returns four different answers from four seeds; see "Free per-level stops do not converge". Any remaining question goes through the exhaustive sweep and the forward-percentile test.
- **Per-side activation is closed.** Measured on the fixed engine and worse in the hypothesis direction; the per-side `min_margin` overrides stay in the engine as inert, tested fields.
- **Switching the config by regime is closed, operator-declared or otherwise.** With perfect regime labels the switched run is 18–19 points below the best fixed config; a regime's outcome is set by the side the bot holds when it begins, which no config can change. See "Switching the config by regime is closed".
- **The activation re-anchor stays, on both sides.** Removing it makes every cycle clean and turns the bot into one that sells once and waits for the price to come back; on 2024-10..2025-03 that is −22 % for all 105 configs with the whole window spent in cash. `reanchor_sell`/`reanchor_buy`/`reanchor_cap_at_entry` remain in the engine as inert switches.
- **A high AUC against a pivot-derived label is not evidence of a signal.** Pivots alternate, so "which leg am I on" is knowable from the trailing return and carries the label with it; any labelling scheme built from future extrema must be checked against a fixed-horizon forward label and a money test before it is believed. This cost one run that looked like a discovery. See "Reverse-engineering the strategy from ideal trades is closed".

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

That argument is right for a single-window score and **wrong for `robust_pnl`**, which is
`min()` over two windows with different holds — two different transformations, and `min()`
of differently transformed values does not preserve order. On the 2025 AUTO run (train hold
+5.21 %, test hold −21.48 %) the top five reorder from `1,2,3,4,5` in euros to `1,3,5,2,4` in
base asset. **But the corrected metric is worse, not better:** in euros the binding half
alternates across candidates; in base asset the *train* half binds for all five, because a
21 % fall makes every config accumulate on the test half (+17 % to +31 %). A two-window guard
collapses into a one-window score whenever the halves sit in opposite regimes, which the
regime swings measured here make the usual case. So: report the base-asset figure beside the
euro one (the euro sign inverted on this run's headline and nearly hid a +14.79 %
accumulation), and do **not** rank by it. The closing sentence of this section still holds —
no objective tweak manufactures a forward signal.

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
config mid-history *meant* calling it again. An `EngineConfig.activation_schedule` carried
`(bar, ActivationParams(k_act, min_margin))` as a step function, the way
`calibration_schedule` already carried the calibration — a new `stop_pct` needed nothing,
since the scheduled `PairCalibration` values are what the percentile produces, but
`min_margin` multiplies each bar's price where `k_stop` multiplies its ATR and cannot be
folded in. Production never set it, and it was removed with the cadence question in
`ca4fc5b`; `calibration_schedule`, which production does not set either but `/backtest` and
the optimizer do, stays.

This mattered most for the cadence question specifically: the number of restarts scales
with the cadence under test, so the artifact landed on the variable being measured and
penalised exactly the arm that reconfigures most.

**The in-tree schedule anchors to the window, production anchors to its own clock.**
`build_calibration_inputs` places its points at multiples of `recalib_bars` from the
*window's* first bar. The live bot recalibrates on a fixed cadence regardless of where an
analysis window happens to start. For a full-history run the two coincide; for a sliced
job they do not. The study's harnesses anchored to the frame, which is the faithful
choice. **Open: decide whether to move the in-tree builder to a frame-anchored grid.**
Given defect 2's sensitivity measurement, this is not cosmetic.

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
  Choppiness Index moved into `config_stability.py`, where it was re-measured without
  restarts and predicted nothing. **Do not cite the +0.44.** The `docs/BACKLOG.md`
  Trend/Chop card this figure motivated is closed on the oracle bound above.

**Still standing:** the structural argument for `MINIMUM_CHANGE_PCT = 0.020` (lowering it
redefines what counts as noise rather than sampling more of it); the `stop_pcts` floor of
0.5; that per-level fitted grids are unnecessary because `stop_pct` is scale-free; that
`n = 0` levels are the live `get_k_stop` fallback's job, not the grid's.

## How to continue

**Thirteen avenues are now closed by measurement**, and none of them was a tuning question —
each was a hypothesis about where the edge lived, and none survived:

| Avenue | Result |
|---|---|
| Selecting by in-sample PnL | percentile 50 of the forward distribution — chance |
| Reconfiguring on a cadence | worse than not reconfiguring, at every cadence |
| Selecting by past consistency | identical to picking at random |
| Gating the bot through rallies | +0.6 points with perfect hindsight |
| Asymmetric stop by side | ±0.4 points, and the effect runs against the mechanism |
| Picking a config from the 0.04–0.07 region | does not replicate at 1-minute resolution |
| Gating on volatility instead of direction | high-ATR stretches are no more directional than low-ATR ones |
| Forcing full allocation through rallies | −3.2 points with perfect hindsight: the gate recovers +17.3 in the rallies and gives back −21.4 in the crash after it |
| Five free per-level `stop_pcts` | the deployed AUTO search does not converge: 0/4 seeds agree after 12 000 trials |
| Per-side `min_margin` (sell reluctant, buy eager) | −5.5 to −7.0 points median, worsening with the asymmetry; the opposite direction is zero |
| Switching the config by regime (lateral / falling / rising), perfect labels | −18 to −19 points below the best fixed config and below the median of the 105, at two labelings; the class winners make 0–1 operations per regime, because the side held when a regime begins decides it and activation cannot change that side |
| Removing the activation re-anchor, or capping it at the sell price | fixes the cycle loss (worst −79.5 % → −1.4 %) but the active configs still trail the median of the grid; on a window where the price does not come back, 105/105 sell once, never rebuy, and land at −22 % |
| Reverse-engineering the rules from ideal trades | the pivot label is worth +406 % traded perfectly, but no causal feature predicts the honest target (sign of the forward return at a fixed horizon: AUC 0.43–0.52 for all 16, price and flow alike); a model scoring 0.767 against the pivot label captures +10.6 of those 406 points at zero fee and −33.1 % at maker |

…but every one of them was measured on XBTEUR, where a config makes 3–7 trades a run. See
USDCEUR below before treating them as settled properties of the strategy rather than of that
sample size.

What is left is not a search for a better config. In order:

1. **Decide whether to run the bot at all, and on what basis.** The strategy has a measured
   conditional edge — it accumulates in flat and falling markets and loses in rallies — and no
   way to tell which is coming. That makes deploying it a directional bet on the market, not
   an edge, and that is a decision for the operator rather than a measurement. There is no
   longer a config recommendation to attach to it: the `min_margin` 0.04–0.07 guidance failed
   to replicate, and the resolution check shows the whole comparison lives inside the
   simulator's own noise. Pick on operational grounds — operation count, fees, time out of the
   asset — and treat the backtested return as an illustration, not a forecast.
2. **Re-run the predictiveness test on USDCEUR.** Done as an external-validity check, it
   turned into the most promising thread in the study: with the pivot threshold and the
   `min_margin` ceiling scaled to the pair, configs make 11–45 operations instead of 3–7. Every
   negative result in this file was measured where the sample was too small to detect an edge
   even if one existed, so *repeat the enumeration and the forward-percentile test here before
   concluding anything about the strategy*. It is cheap: the whole grid at three resolutions is
   about six minutes.
3. **Implement the base-asset denomination** as a request flag (`objective: "EUR" | "BASE"`).
   Reporting only, until an objective compares across windows again — see that section for
   what it does and does not change.
4. **Decide whether shared `stop_pct` ships to production.** If it does, the deployed space
   becomes enumerable and Optuna/TPE/seeds/AUTO can be removed — a large simplification of
   `trading/optimizer/`. That decision needs its own spec.
5. **Unify the activation branches** into one two-dimensional space (defect 5). Strategy
   change: it touches `activation_distance` in both `trading/engine.py` and
   `trading/positions_manager.py`, and needs its own validation. Lower priority now that
   `k_act` is out of the experiments, but it is still the reason the profitable region only
   exists in one branch.
6. **Decide the schedule anchoring** (harness defects). Frame-anchored is faithful;
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

Five were deleted in `ca4fc5b` once their question closed — `holdout_experiment.py`,
`refit_frequency_experiment.py`, `config_stability.py`, `objective_experiment.py` and
`grid_validation.py`. Their results are recorded above and are **not** re-derivable from
the tree; each section that reports one names the script that produced it. What survives is
what still answers a question no result has closed.

| Script | What it answers |
|---|---|
| `scripts/import_kraken_ohlcvt.py` | Loads Kraken's CSV archives into `ohlc_data` (REST only returns ~720 candles). |
| `scripts/analysis/execution_fidelity.py` | **How much of a result is the simulator's 15-minute clock?** Runs the same configs at 15/5/1 min with ATR and the calibration schedule held fixed on the 15-minute series, so only the evaluation cadence varies. Reports per-config deltas, the rank correlation between arms, the top-N overlap, and whether the effect converges. Also the pair-portability harness: `--pair`, `--fee`, `--mm-max` (the `min_margin` grid is a fraction of *price*, so its ceiling must scale with how far the pair moves) and `--min-change-pct` (an ATR multiple in disguise — see the USDCEUR section). Reads the CSV archives directly; takes the data directory, not `--csv`. |
| `scripts/analysis/grid_sweep_holdout.py` | **Enumerates all 105 configs** in-sample and forward, and reports where the in-sample winner lands in the forward distribution, at several decision dates. No sampler, no seed. Reports euros and base asset. `--csv`. |
| `scripts/analysis/side_margin_sweep.py` | **Does selling reluctantly and rebuying eagerly accumulate base asset?** Paired sweep: each symmetric config against its per-side `min_margin` neighbours at three δ in both directions, one continuous run each; reports the delta distribution. Takes the 15-minute CSV path. |
| `scripts/analysis/run_optimizer_csv.py` | **The deployed optimizer, against the CSV archives.** Builds the same `OptimizerRequest` the route accepts and runs `OPTIMIZE` (the enumeration; AUTO is retired) in process with the OHLC loader and calibration cache patched; writes the result to `--out` before printing. |
| `scripts/analysis/cycle_decomposition.py` | **Where does the loss of operating come from?** Pairs every sell with its rebuy across the 105 configs and scores each cycle in base asset with fees; reports wins and losses against the `mm − fees` floor per `min_margin`, plus time in cash. No new simulation beyond the sweep. Takes the 15-minute CSV path. |
| `scripts/analysis/signal_screen.py` | **Does anything visible at `t` predict the ideal action, and is it worth money?** Three parts that must all pass: what the perfect pivot label is worth when traded, per-feature AUC against both the pivot label and a fixed-horizon forward label, and the model's prediction run as an allocation in base asset at zero and maker fees. Causal features in two families (price/volatility as control, flow — volume and trade count — as the untested one), circular-shift null, temporal split fixed in advance. No engine, no configs; minutes to run. |
| `scripts/analysis/reanchor_ablation.py` | **Is the bot viable without the activation re-anchor?** The 105 configs under production, no buy re-anchor, no re-anchor on either side, and the re-anchor capped at the leg's entry price; reports the base-asset distribution, the per-`min_margin` medians, time in cash and how many configs end the window in cash. Run it on both a window where the price came back and one where it did not; `--fee` sweeps the fee per trade. Takes the 15-minute CSV path. |
| `scripts/analysis/regime_switch_oracle.py` | **What is switching the config by regime worth, with perfect labels?** Labels the window by shape (impulse-first: M % in ≤ K days; ≥ D-day gaps are lateral), ranks all 105 configs per class from sliced continuous runs, and runs the switched config as one continuous run against the best fixed, the recommended, and the median — with and without full allocation through rallies. `--move-pct`, `--max-days`, `--min-days`, `--active`, `--labels-only`. Takes the 15-minute CSV path. |
| `scripts/analysis/rally_gate_oracle.py` | **What is a perfect rally detector worth?** Gates whole rally periods with hindsight and forces full allocation through them (`force_hold_bars`), re-simulated continuously — never as an overlay. Reports the arms and the per-period breakdown that separates what the gate recovers from what it costs downstream. Takes the 15-minute CSV path. |
| `scripts/analysis/volatility_regime_screen.py` | **Are high-ATR stretches more directional than low-ATR ones?** Kaufman efficiency ratio by volatility level over non-overlapping windows at five horizons, against the `1/√N` random-walk null. No engine, no configs, no fees — a descriptive measure of the market, seconds to run. Takes the data directory, not `--csv`. |
| `scripts/analysis/grid_derivation_explore.py` | Reports the structural distributions behind each grid (K per level, leg/ATR, ATR/price) — the inputs the `SearchSpace` defaults are drawn from. |

The point caches exist because the in-tree `build_calibration_inputs` recomputes on every
call, and a schedule over a long window costs minutes — 280–640 s for the 15-month XBTEUR
frame (909 points), varying with machine load. `grid_sweep_holdout.py` computes the
frame's points once and slices them per window (frame-anchored); the deleted harnesses did
the same, except `objective_experiment.py`, which memoized per window since every arm and
every seed of a transition re-fit the same one. All were installed by monkeypatching
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
- **Report the operation count next to every result.** A config that made 3–7 trades in 275
  days can post +27 % base-asset accumulation on luck alone, and every "dead" effect this
  study measured (+0.6 for the trend filter, ±0.4 for the asymmetry) is far below the ±9
  points that changing only the simulator's clock produces. A difference smaller than the
  harness's own dispersion is unresolved, not disproved — the action is the same, but the
  claim must not be stated as knowledge.
- **The simulation's bar interval is a parameter of the bot, not of the measurement.**
  Production evaluates the trailing stop every `SLEEPING_INTERVAL` (60 s). A 15-minute
  simulation ratchets it fifteen times less often and is a different strategy. Anything
  re-run from here runs at 1 minute; it costs 186 s for 105 configs over 275 days, against
  30 s at 15 minutes, and the calibration schedule — the expensive part at 185 s — is shared.
