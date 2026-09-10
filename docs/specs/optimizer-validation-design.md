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

### Three years, and what the recommended config actually does (2026-09-08)

Every figure above was measured on nine months of 2025, and the owner put the obvious
objection: a config making five operations wins by picking two moments, which is a sample
of size five, and *more* operations would make luck matter less. Both halves are right, and
the second one only holds when the expected value per operation is positive — the cycle
decomposition says it is not. So: the same four arms over 2023, 2024 and 2025 separately,
and over one continuous run of all three.

| Window | hold (EUR) | production median | beats hold | best | `mm=0.05/0.9` | `mm=0.07/0.5` | `mm=0.00/0.9` |
|---|---|---|---|---|---|---|---|
| 2023 | +149 % | −55.3 % | 0/105 | −44.1 % | −56.6 % (1 op) | −57.4 % (1) | −57.9 % (100) |
| 2024 | +134 % | −48.6 % | 0/105 | −29.9 % | −49.9 % (3) | −45.4 % (5) | −76.3 % (143) |
| 2025 | −17 % | +24.4 % | 75/105 | +59.0 % | +32.7 % (10) | +42.4 % (10) | −42.9 % (128) |
| **2023-01 .. 2025-12, continuous** | **+384 %** | **−76.0 %** | **0/105** | −59.4 % | **−78.0 %** (4) | −78.4 % (4) | −93.0 % (366) |

The recommended config loses **78 % of the base asset over three years**, in four
operations. It did not time the market; it made one sell in the one year that fell, and the
same behaviour costs half the coins in each of the two years that rose. Nothing in the grid
beats holding in 2023, in 2024, or over the three years together — 0 of 105 under all four
re-anchor arms.

The arms without the chase collapse to a single behaviour over three years: **1.0 operations,
99 % of the window in cash, 105/105 ending in cash, −77 to −79 %**. They sell once in early
2023 and never rebuy, because the price never returns. Removing the chase bounds the
per-cycle loss and, over a long rising span, converts the bot into a one-shot exit.

On the operation-count argument: it is confirmed, in the direction that hurts. In 2023 and
2024 the configs making 100–784 operations do not scatter around the passive ones — they
converge *below* them (`mm=0.00` at −93.0 % over three years against −78.0 % for
`mm=0.05`), which is what a negative expectation per operation looks like when you take more
draws from it. Frequency reduces variance around a mean that is negative.

What the three years establish, and it is the study's closing number: in base asset a run
returns roughly `−drift × time in cash + convexity − fees`, and with drifts of +134 % and
+149 % the first term is an order of magnitude larger than the other two. The bot is a bet
on flat or falling years — it gains a quarter to a third of the coins in the year that falls
and loses half in each year that rises — and no configuration, re-anchor variant, operation
frequency or fee level changes that, because none of them changes the first term.

### The rebalancing premium does not survive the drift either (2026-09-08)

Closing the study, the owner asked what to do with the bot. One suggestion made it into
that answer: if the point is to capture what the bot *actually* does that has value --
spend time out of the asset and re-enter lower -- then the cheapest substitute is a
constant-mix portfolio rebalanced by threshold, which harvests the same convexity with a
handful of trades a year and no chase. The figure quoted, ~2 % a year in base asset, came
from the closed form `0.5 * w(1-w) * sigma^2`. That form assumes a driftless random walk.
This document has already recorded four occasions where a formula lost to a measurement, so
`scripts/analysis/constant_mix_rebalance.py` measures it: no engine, no configs, no
calibration -- the rule is a function of the close and nothing else, checked every 15-minute
bar, which is the most favourable sampling available.

The measurement needs two benchmarks kept apart, and conflating them is the trap:

| | |
|---|---|
| vs holding (100 % asset) | the study's benchmark. A 50 % portfolio carries half the exposure, so it loses to holding in any rising window *by construction*. This says nothing about the rule. |
| vs the same static mix | same initial weights, never rebalanced. Identical direction, identical exposure, the rebalancing the only difference. This is the premium, and it is denomination-free -- both sides divide by the same final price. |

Against the second benchmark, at `w = 0.5`, XBTEUR:

| window | hold | band 2 % | band 5 % | band 10 % | band 20 % | rebalances (2 %) |
|---|---|---|---|---|---|---|
| 2023 | +150 % | −7.65 % | −7.68 % | −5.83 % | −1.33 % | 26 |
| 2024 | +134 % | −5.30 % | −5.48 % | −4.48 % | −0.02 % | 41 |
| 2025 | −17 % | **+1.93 %** | +1.01 % | 0.00 % | 0.00 % | 30 |
| 2023–2025 | +384 % | **−18.36 %** | −17.40 % | −18.26 % | −10.06 % | 99 |

**The premium is negative in every rising window and worth +2 % in the one falling year.**
It is not the fee: at zero fee the three-year figure is −17.62 % against −18.36 % at 0.4 %,
so 0.8 points of the 18 are commission and the other 17 are drift. The band behaves the way
the mechanism predicts once that is seen -- wider bands rebalance less and lose less, and at
`w = 0.75 / band 20 %` (one rebalance in three years) the premium finally turns positive at
+6.0 %, which is to say the best version of the rule is the one that barely applies it.

The reason is the same sentence the rest of the study keeps arriving at. Rebalancing sells
the asset that rose and buys the one that fell; against a positive drift that is a
systematically losing trade, and `0.5 * w(1-w) * sigma^2` only exceeds it when the drift is
near zero. In base asset a constant-mix run returns roughly `−drift × time out of the asset
+ convexity − fees`, which is *the same expression* as the bot's, with the chase removed and
the harvest bounded. Removing the chase removes the pathology and leaves the first term
untouched -- and the first term is the one that decides the outcome.

So the recommendation was wrong and is withdrawn: a threshold-rebalanced fixed allocation is
not a cheaper way to capture what the bot captures. It is the same bet, cleanly expressed,
and the bet loses whenever the asset rises. What it does establish is a floor for the whole
family: **any signal-free allocation rule over these data is bounded above by about +2 % a
year, and only in a falling market.** No amount of engineering on the rule's mechanics
reaches the +384 % the window handed to anyone who did nothing.

### Under a monthly DCA the benchmark changes, and placement still loses (2026-09-08)

The owner reframed the goal: the euros are not a lump sum, they arrive as a fixed monthly
contribution. That is a genuinely different question, because the benchmark stops being
buy-and-hold from `T0` and becomes the **naive DCA itself**. It is also a fairer fight in
one respect and a harder one in another: fairer because the bot is no longer competing with
a single perfect entry, harder because a fixed-euro monthly purchase already buys more coins
when the price is low -- it *is* the convexity harvest, bought for free, with no rule.

`scripts/analysis/dca_overlay.py` measures what automating the *placement* of that purchase
adds. No engine and no prediction: every arm contributes exactly the same euros and differs
only in where the buy lands. Scored as final value (coins at the final price plus unspent
cash) over euros contributed.

| arm | 2018–2025 | 2021–2025 | 2023–2025 |
|---|---|---|---|
| day 1, whole amount | **reference** | **reference** | **reference** |
| split weekly | −0.54 % | −0.71 % | −2.84 % |
| split daily | −0.70 % | −0.82 % | −2.91 % |
| limit −5 %, market at month end if unfilled | −2.91 % | −0.71 % | −2.99 % |
| limit −10 % | −1.90 % | −0.87 % | −2.63 % |
| limit −20 % | −0.79 % | −1.77 % | −5.80 % |
| double on a 20 % drawdown, half otherwise | −7.69 % | −7.66 % | −21.37 % |

**Buying the whole contribution on day one wins in all three windows.** The mechanism is the
one this document keeps arriving at: every arm delays exposure, and against a positive drift
delay is a systematically losing trade. The dip-limit arms make it explicit -- when the limit
fills you save the dip, and when it does not you buy at the month's close, which under drift
is the more common and more expensive branch.

One honesty note on the last arm: it holds cash back to fund the doubling, so part of its
loss is under-deployment rather than mistiming, and it is not a clean placement test. The
split and limit arms are clean -- same total, fully deployed, only the placement differs.

The fee lever, by contrast, is small, certain and positive: the same naive DCA over
2018–2025 is worth +0.24 % moving from taker (0.40 %) to maker (0.16 %). That is the only
overlay measured in this study with a positive expected value, and its size is a quarter of
one percent over eight years.

What this settles for the DCA framing: **the bot's contribution is execution, not timing.**
Placing the buy, paying the maker fee, keeping the record, running the alerting -- all real,
all small, none of it alpha. And layering the trading strategy on top of a DCA stack is not
a separate question: the three-year run already measured that behaviour at −76 % of base
asset, and a stack that grows by contribution changes the size of the bet, not its sign.

### The trailing entry on a DCA buys cheaper 9 months in 10 and still loses (2026-09-08)

The owner pushed back on the previous section, correctly: the arms measured there used
*fixed* limits, and a fixed limit never fills in a month that only rises, whereas the bot's
trailing entry always fills, because any bounce triggers it. That is a different mechanism
with a different trade-off -- pay the bounce every month, collect the intramonth drawdown
when there is one -- and it deserved its own measurement rather than an argument.
`scripts/analysis/dca_trailing_entry.py` runs it on the 15-minute bars, which is the
resolution the live bot ticks at: track the running low from the month's open, buy when the
price rises `bounce` x ATR off it, optionally requiring a fall of `fall` x ATR first, and
buy at the month's close if the month ends without a trigger.

Two families, and they fail in opposite ways.

| arm | 2018–2025 | 2021–2025 | 2023–2025 | median wait | entered below day 1 | never triggered |
|---|---|---|---|---|---|---|
| bounce 0.5x ATR | −0.03 % | −0.09 % | +0.14 % | 0.8 h | 38/96 | 0/96 |
| bounce 1x ATR | +0.05 % | −0.13 % | +0.12 % | 1.5 h | 34/96 | 0/96 |
| bounce 3x ATR | +0.10 % | −0.07 % | +0.16 % | 6.8 h | 36/96 | 0/96 |
| fall 1x + bounce 1x | −0.85 % | −1.18 % | −1.78 % | 5.8 h | **80/96** | 4/96 |
| fall 2x + bounce 1x | −0.58 % | −1.89 % | −1.94 % | 9.8 h | **87/96** | 7/96 |
| fall 3x + bounce 1x | −1.22 % | −2.15 % | −1.81 % | 15.8 h | **87/96** | 9/96 |

**The pure trailing entry does not lose -- because it does not wait.** Its median wait is
0.8 to 6.8 hours and it never once reaches the month's close untriggered in 96 months: the
price bounces `bounce` x ATR off its running low almost immediately, so the rule degenerates
into buying on day one, and lands within ±0.16 % of it. It is not a better entry, it is the
same entry wearing a mechanism. This is the more useful half of the result, because the rule
*looks* like it should wait and the wait column is what shows it does not.

**Make it actually wait, and it buys cheaper almost every month and still loses.** Requiring
a fall first is what binds, and it works exactly as intended on the metric it was designed
for: with `fall 2x`, 87 of 96 months entered below the day-one price -- a 91 % hit rate on
"did I get a better price" -- and the arm still finishes 0.58 points behind buying on day
one, and 1.9 points behind over the two shorter windows. The 7 to 9 months that never
trigger buy at the month's close after a rally has already happened, and those few months
cost more than ninety small victories return.

That is worth stating plainly because it is the shape of every negative result in this
document: **a high hit rate with a negative expectation.** The owner's intuition about the
mechanism was right -- it does follow the price down and it does buy the bounce -- and the
mechanism does what it promises. What it cannot do is change the sign, because the months it
loses are the months the price ran away, and those are the months that carry the drift.

### Immediate activation is closed; free stops move no distribution (2026-09-08)

Two gaps the owner spotted in the record, and they were real gaps. The `k_act` branch is
disabled in every sweep in this document -- all 105 configs carry `k_act=None` -- so
`k_act = 0`, immediate activation, had never been evaluated at all; what stood against the
branch was the structural argument in defect 5 plus a stale count of 12 hold-out fits. And
the free per-level stops were measured once, by Optuna, on 2025 with `min_margin` pinned at
0.004, returning `converged: False` -- which says the *search* does not identify an optimum,
not that the region lacks one. `scripts/analysis/kact_and_free_stops.py` puts all three
groups on one calibration schedule and one window so they are comparable, on 2025 (hold
−17.41 %, a falling year and therefore the bot's favourable case).

| group | median | p25 | p75 | best | worst | beats hold | ops | in cash |
|---|---|---|---|---|---|---|---|---|
| 105 shared (reference) | +24.4 % | −0.4 % | +35.3 % | +59.0 % | −88.4 % | 75/105 | 23.7 | 38 % |
| `k_act` branch | −80.7 % | −95.2 % | −32.9 % | +30.3 % | −99.6 % | 5/35 | 499.7 | 45 % |
| 600 free stops | +24.6 % | −0.4 % | +35.8 % | +73.5 % | −83.4 % | 423/600 | 27.2 | 40 % |

**Immediate activation is the worst configuration the strategy has.** Broken out by
multiplier, the branch is monotone and the direction is the study's:

| `k_act` | median | best | worst | beats hold | ops |
|---|---|---|---|---|---|
| **0** | **−95.3 %** | −80.7 % | −99.6 % | 0/5 | **807** |
| 1 | −94.9 % | −80.5 % | −99.5 % | 0/5 | 776 |
| 4 | −80.4 % | −65.6 % | −88.1 % | 0/5 | 378 |
| 8 | −29.7 % | −2.7 % | −33.0 % | 0/5 | 84 |
| 16 | +19.4 % | +30.3 % | +12.0 % | 5/5 | 15 |

At `k_act = 0` the activation distance is zero, the stop arms at the entry price, and the
bot trades **807 times in a year** to lose 95 % of the base asset -- in the one year of the
three that holding lost money. Defect 5's structural argument is now a measurement: with no
ATR-independent floor the barrier collapses and the whole branch degenerates into churn.
Only `k_act = 16` beats holding, at 15 operations, which is the study's standing result that
what helps is trading less, not a property of the branch.

**Freeing the five levels moves the best config and nothing else.** Median +24.6 % against
+24.4 %, p25 identical, p75 +35.8 against +35.3, and the share beating hold 70.5 % against
71.4 %. The distributions are the same distribution. What does move is the maximum, +73.5 %
against +59.0 %, and that is what 600 draws from a superset do to 105 draws from a subset
even when nothing is there: the shared space is a point set *inside* the free space, so its
maximum can only be lower, and the gap is an order statistic, not a region.

That is as far as this run goes, and it is worth being exact about the boundary. It closes
"do free stops shift the distribution" -- no -- and it does **not** close "does the free
maximum survive forward", because +73.5 % is an in-sample maximum and avenue 1 of this
document already measured in-sample selection landing at percentile 50 of the forward
distribution. Settling that needs the fit-forward test run on the free space, not another
in-sample sweep.

### One config, eight years, only against hold (2026-09-08)

Every fee reading in this document came from one window, 2025-04..2025-12, picked because
the price came back -- the most favourable stretch in the data. The owner asked for the same
configuration on intervals that were not chosen for their result, compared against nothing
but holding. `scripts/analysis/capped_mm0_across_years.py` runs exactly that: `min_margin =
0`, `stop_pct = 0.9` on all five levels, `reanchor_cap_at_entry = True`, on the eight
calendar years 2018-2025, each calibrated from six months of prior history, scored in base
asset at 0.40 % and at Kraken's 0.16 % maker rate. No grid, no median, no other candidate.

| year | hold (EUR) | base @0.40 % | base @0.16 % | ops | in cash |
|---|---|---|---|---|---|
| 2018 | −72.6 % | **+67.3 %** | **+89.1 %** | 50 | 16 % |
| 2019 | +97.6 % | −49.7 % | −48.5 % | 9 | 98 % |
| 2020 | +269.8 % | −72.3 % | −71.5 % | 11 | 95 % |
| 2021 | +72.7 % | −27.3 % | −21.1 % | 33 | 77 % |
| 2022 | −62.2 % | **+11.8 %** | **+21.6 %** | 34 | 13 % |
| 2023 | +149.3 % | −58.9 % | −58.7 % | 1 | 99 % |
| 2024 | +133.5 % | −53.9 % | −52.5 % | 11 | 94 % |
| 2025 | −17.4 % | **+4.4 %** | **+10.8 %** | 24 | 22 % |

**The sign separation is perfect, 8 of 8.** The configuration beats holding in exactly the
three years holding lost money and loses in exactly the five it made money. Nothing in the
study has separated this cleanly, and it is not a coincidence to be explained away -- it is
the mechanism already established (the side held when a regime begins decides the regime)
observed one year at a time on data that were not selected.

**Time in cash is bimodal and it is the whole story.** In the three winning years the bot is
in cash 13-22 % of the time: it holds the asset and cycles productively, 24-50 operations,
selling into strength and rebuying lower. In the five losing years it is in cash 77-99 %: it
gets stopped out early in a rise and, with the re-anchor capped at the sell, never re-enters
because the price never returns. 2023 is the pure case -- **one operation, 99 % of the year
in cash, −58.9 %**. It sold once in January and watched the price two and a half times away.

**The maker rate changes magnitude, never sign, and the gain scales with turnover.** Moving
from 0.40 % to 0.16 % is worth +21.8 points in 2018 (50 ops), +9.8 in 2022 (34 ops) and +6.4
in 2025 (24 ops), and under 2 points in every year the config barely trades. That is the
rotation threshold from the fee sweep showing up again on unselected data: the fee only
matters where the cycles are, and the cycles are only in the falling years.

**What it is, stated plainly.** Median −21.1 % at maker, 3 of 8 years positive, best +89.1 %,
worst −71.5 %. This is not a strategy with an edge; it is a clean conditional instrument
whose payoff is known and whose condition is "the year falls" -- a synthetic short-drift
position denominated in the base asset. The magnitudes are large enough to be useful to
somebody who holds a directional view, and this document has already measured, twice, that
the bot cannot supply that view. Chaining the yearly factors is not a valid eight-year
result (each year restarts the position; see the harness defect on segmented scoring); the
continuous three-year run is the one that stands, at −76 %.

### Holding by default and trading only the ranges (2026-09-09)

The owner's proposal after the eight-year table, and it is not any of the three gates already
measured: invert the default. Outside a lateral stretch the bot **holds the asset and does not
trade** -- which is exactly 0 % in base asset by construction -- and trading is enabled only
once a range is confirmed. Every earlier gate defaulted to trading (the regime oracle switched
configs but always traded; the rally gate forced allocation during rallies only). The
inversion also fixes what killed the regime oracle: the bot now enters every lateral stretch
*holding the asset*, which is the correct side to harvest a range from.

`scripts/analysis/lateral_gate_oracle.py` measures the ceiling with perfect labels -- no
detector, since a detector can only lose against an oracle. The gate is `force_hold_bars` over
every non-lateral bar, labels from `regime_switch_oracle` (M = 10 %, K = 7 d, D = 7 d), XBTEUR
2025 (hold −17.41 %). The year labels out at 9 lateral stretches, 271 days, **74 % of the
year**, median 24 days -- the owner's premise about how much time an asset spends ranging is
correct. Four arms, the last two testing the two modifications he proposed alongside it:

| arm | median | best | beats hold | ops | in cash |
|---|---|---|---|---|---|
| no gate (reference) | +24.4 % | +59.0 % | 75/105 | 23.7 | 38 % |
| gate, live anchor | **+40.2 %** | **+200.6 %** | 87/105 | 23.5 | 20 % |
| gate, reset on open | −0.4 % | +41.6 % | 38/105 | 18.4 | 8 % |
| gate, reset + local calibration | −0.4 % | +55.1 % | 40/105 | 21.0 | 8 % |

**The +200.6 % is the oracle leaking, and the arms are built to prove it.** Under the mask the
trailing stop keeps tracking, so it follows an impulse to its high; when the oracle declares
the impulse over and opens the gate, the bot exits at a stop anchored to a top nobody could
know in advance. `reset_on_unmask` (a new inert `EngineConfig` switch, off in production)
reopens the leg at the bar the gate lifts and changes **nothing else**. That single variable
takes the best config from +200.6 % to +41.6 % and the share of sells landing within a day of
a gate opening from **9 of 21 to 1 of 8** — against 0 of 5 for the ungated arm. A controlled
comparison isolating the one path that carries information out of the mask.

**The honest arm is worse than not gating at all**, and the decomposition says exactly why:

| arm (best config) | lateral | rising | falling | sells < 1 d after opening |
|---|---|---|---|---|
| no gate | **+43.7 %** | −1.5 % | **+14.6 %** | 0/5 |
| gate, live anchor | +202.9 % | −0.7 % | 0.0 % | 9/21 |
| gate, reset on open | **+41.9 %** | +0.2 % | −0.4 % | 1/8 |
| gate, reset + local calibration | +54.8 % | +0.2 % | 0.0 % | 0/8 |

**Gating does not improve the range harvest — it is the same harvest.** Ungated, the bot earns
+43.7 % inside the lateral stretches; gated with a clean restart, +41.9 % inside those same
stretches. The premise that the trends damage the harvest is measured and false: the harvest is
unchanged. What the gate does is delete the falling-segment contribution, +14.6 %, because a
falling impulse is not lateral and the gate is shut through it. The proposal trades away the
only segment class the bot reliably wins in, and buys nothing with it.

That is the eight-year table restated at segment resolution. The bot's gains come from falling
markets; a lateral-only gate is fully in the asset through every fall.

**The two modifications, measured on their own terms.** Calibrating a range only from data
since the range began (the owner's hypothesis that the prior impulse pollutes the calibration)
does not move the distribution — median identical, 38/105 to 40/105 — and moves the best
config from +41.6 % to +55.1 %, an in-sample maximum over 105 draws, with 168 of 506 local
points falling back to the global calibration for want of events. Not evidence. The stop
question does move: the best config goes from `mm=0.08 stop=0.8` (10 ops) ungated to
`mm=0.06 stop=0.5` (16 ops) gated — a narrower barrier and a much shorter stop, the direction
range-harvesting predicts. It is one in-sample maximum, so it is a hint about where to look,
not a result.

**Then the owner objected to the median, and the objection was right.** The argument: in
production the bot runs *one* config, not the distribution, and choosing it is within our
control — so a median is the wrong estimator. Everything above answered that with avenue 1
(selection by in-sample PnL lands at percentile 50 forward, i.e. choosing carries no
information), but avenue 1 was measured over whole years. The sharper version of the
objection is that a lateral stretch is a more homogeneous thing than a year, so selection
might work *inside* ranges even where it fails across them. That is measurable, and it had
not been measured. `selection_test` fits on the first 4 lateral stretches and scores the
pick on the last 5, reporting where it lands among all 105:

| arm | best consistency | pick: fit | test | percentile | best available |
|---|---|---|---|---|---|
| no gate | 7/9 | +26.7 % | +17.5 % | **63 %** | +34.1 % |
| gate, live anchor | 8/9 | +57.4 % | +92.4 % | 96 % | +97.5 % |
| gate, reset on open | **9/9** | +12.9 % | **+25.7 %** | **93 %** | +30.5 % |
| gate, reset + local calibration | **9/9** | +14.5 % | +23.2 % | 85 % | +38.6 % |

**Inside ranges, choosing works.** The honest gated arm's pick lands at percentile 93 and
captures +25.7 of the +30.5 available, against percentile 63 ungated. And a config exists
that beats hold in **all nine** lateral stretches, where the ungated best manages 7 of 9 and
the regime oracle's earlier lateral class had no config winning more than 4 of 6. Both
signals point the same way: the gate makes the config decision easier, which is exactly what
the owner argued and the opposite of what the median suggested.

**What that does and does not settle.** It is one window, one year, nine segments, one
selection decision — under a null of random selection, landing at percentile 93 or better has
probability about 0.07, so this is a hint at the edge of noise, not a finding. The `live
anchor` arm reaching percentile 96 is a caution rather than support: a leak can be
selectable too. And the comparison is incomplete in a way that matters — the table scores
*lateral segments only*, which for the gated arm is the entire strategy but for the ungated
arm omits the falling segments where it earns most. The completing measurement is the same
forward split scored over the whole span rather than the lateral slices.

**So the verdict is downgraded, not reversed.** What stands: the leak, and that the lateral
harvest is unchanged by gating (+41.9 % against +43.7 %). What does not stand: "the gate is
worse than not gating", which rested on the median and is now the wrong lens for a gated
arm whose selection demonstrably carries information. This avenue is **not closed** — it is
the only open one in this document with a measurement pointing in its favour.

### 2024 confirms the gate's mechanism and refutes the config choice (2026-09-09)

2025 was a falling year, which is the bot's favourable case, so the gate was re-run on 2024:
hold **+133.5 %** in euros, and the ungated bot's worst measured year, −48.6 % of base asset
with **0 of 105** configs beating hold. The owner's claim was that giving up the falling-year
gains also gives up the rising-year losses. Labels: 9 lateral stretches, 183 days, 50 % of the
year (against 74 % in 2025), 9 rising and 3 falling.

| arm | median | best | beats hold | ops | in cash |
|---|---|---|---|---|---|
| no gate | **−48.6 %** | −29.9 % | **0/105** | 26.0 | 86 % |
| gate, live anchor | +2.5 % | +82.9 % | 100/105 | 20.4 | 12 % |
| gate, reset on open | **−0.4 %** | **+26.4 %** | **38/105** | 13.1 | 5 % |
| gate, reset + local calibration | −0.4 % | +33.7 % | 37/105 | 14.9 | 6 % |

**The mechanism is confirmed, and the size of it is the finding.** A −48.6 % median year
becomes −0.4 %, and 0 of 105 beating hold becomes 38 of 105. The decomposition names the
transfer exactly: ungated, the bot earns +54.0 % inside the lateral stretches and gives back
**−67.8 % in the rising ones**; gated, the rising contribution is −0.7 %. The claim that
holding through trends converts the rising-year losses to zero is measured and true, on the
year that punishes the strategy hardest. The leak diagnostic replicates too — 8 of 20 sells
at the gate's edge with the anchor live, 1 of 12 with the reset, against 0 of 13 ungated.

**And the config choice does not transfer.** The selection test (fit on the first 4 lateral
stretches, score the pick on the last 5) lands at **percentile 63** with +3.8 % of the +21.5 %
available, against percentile 93 and +25.7 of +30.5 in 2025. Two windows, one hit and one
miss, which is what selection looks like when it carries no information. The 2025 result must
now be read as the draw it was: at p about 0.07 for a single test, one of two windows landing
high is unremarkable.

**But the failure has a named cause, and it is not the same as "selection is impossible".**
Naming the configs shows the criterion picking against itself:

| arm | picked by the fit | wins | most consistent | wins |
|---|---|---|---|---|
| no gate | `mm=0.040 stop=0.5`, 28 ops | 5/9 | `mm=0.020 stop=0.7`, 42 ops | 7/9 |
| gate, reset on open | `mm=0.060 stop=0.6`, 8 ops | **4/9** | `mm=0.020 stop=0.6`, 24 ops | **7/9** |
| gate, reset + local | `mm=0.050 stop=0.5`, 10 ops | 8/9 | `mm=0.020 stop=0.9`, 22 ops | 9/9 |

Selecting by compounded return over the fit half picked a config that beats hold in **4 of 9**
stretches while one beating hold in **7 of 9** sat in the same grid. Compounded return is
dominated by whichever stretch happened to be largest; it is a sum, and the thing worth
selecting for is a rate. That is a defect of the selection rule, measured, and it is testable
on its own — refit selecting by segments-won rather than by compound and see whether the
percentile moves. Until that is run, "choosing works inside ranges" is unsupported and
"choosing cannot work inside ranges" is equally unsupported.

**One caution for reading the percentile column at all.** The ungated arm reaches percentile
98 in 2024 — inside a year where every one of its 105 configs loses to hold. A percentile is
a rank within a distribution, not a return, and the selection table scores *lateral segments
only*, which is the whole strategy for a gated arm and a fragment of it for an ungated one.
Neither number should be read across arms.

**Where this leaves the avenue.** The half that is about the *bot* is confirmed on two years
in opposite regimes: gating to ranges removes the trend exposure in both directions, and in
base asset that is worth +48 points in a rising year and costs the falling-year gains, as
designed. The half that is about *us* — picking the config, and later building a causal
detector — has one failed test and one that no longer means much. The next measurement is the
selection criterion, because it is cheap and the current one is demonstrably broken.

### 2023 held out: the gate holds a third time, and the config region transfers (2026-09-09)

Two questions were open after 2024: is there a config that works in ranges across regimes,
and is 18-of-18 remarkable or ordinary? Both needed a year that took no part in choosing
anything. 2023 — hold **+149.3 %** in euros, another strong rising year — was run with the
candidates fixed in advance and never re-selected.

**The gate, confirmed a third time.** Ungated: median −55.3 %, **0 of 105** beating hold.
Gated with the reset: median **+0.7 %**, **54 of 105** beating hold, best +22.9 %. The
decomposition repeats exactly: ungated the bot earns +16.9 % in the lateral stretches and
gives back **−60.9 % in the rising ones**; gated, the rising contribution is −0.4 %. Three
years, three regimes, one behaviour. In 2023 more than half the grid beats holding once the
trends are masked out.

**The base rate, which was the missing piece.** Segments won by an arbitrary config:

| arm | median | p90 | max | perfect |
|---|---|---|---|---|
| no gate | 5/8 | 5/8 | 7/8 | 0/105 |
| gate, reset on open | **1/8** | 5/8 | 6/8 | 0/105 |
| gate, reset + local calibration | **1/8** | 5/8 | 6/8 | 0/105 |

Under the gate the median config wins **one stretch in eight**. The grid is mostly bad at
ranges, so the choice matters a great deal — and no config is perfect in a year it did not
help select, which is the honest counterweight to the 18-of-18 headline.

**The candidate holds on money, not on dominance.** `mm=0.020 stop=0.9`, fixed by 2024–2025:
in 2023 it wins **5 of 8** stretches (grid median 1, grid max 6) and compounds **+23.5 %**
against the best config in the whole grid at +23.8 % and the grid median at +0.7 %. So it
landed at the top of the grid in a year it never saw — but the "beats hold in every stretch"
property did **not** replicate. It wins most stretches and loses small in the rest.

**And it is a region, not a lucky point.** All eight of the cross-year top configs from the
local-calibration arm, evaluated in 2023 in that same arm:

| config | wins | compound | | config | wins | compound |
|---|---|---|---|---|---|---|
| `mm=0.020 stop=0.9` | 5/8 | **+23.5 %** | | `mm=0.020 stop=0.8` | 5/8 | +17.4 % |
| `mm=0.020 stop=0.5` | 5/8 | +13.1 % | | `mm=0.020 stop=0.6` | 4/8 | +12.8 % |
| `mm=0.030 stop=0.9` | 5/8 | +14.6 % | | `mm=0.050 stop=0.5` | 4/8 | +21.5 % |
| `mm=0.030 stop=0.8` | 4/8 | +14.5 % | | `mm=0.030 stop=0.7` | 4/8 | +12.6 % |

Every one lands at 4–5 wins against a median of 1, and every compound sits between +12.6 %
and +23.5 % against a median of +0.7 %. Eight neighbouring grid points are not eight
independent tests — they are correlated by construction — but a lucky single point would not
drag its neighbours with it. The `mm = 0.02–0.03` band with a wide stop is a region of the
grid that works in ranges across three years and two regimes.

**Selecting within one year still fails; selecting across two works.** The fit-on-half
test lands at percentile 55 and 54 in 2023 and 63 in 2024, against 93 in 2025 — noise. But
the configs chosen on the 18 stretches of 2024–2025 transferred to 2023. The difference is
sample size: four stretches decide nothing, eighteen do.

**What is now established, and what blocks it.** Established on three years: masking the
trends removes the bot's trend exposure in both directions, more than half the grid beats
holding once it is masked, and a region of the grid identifiable from past years lands near
the top of a held-out one. Not established: everything downstream of a **causal detector**.
Every number here comes from labels that look seven days into the future. The ceiling is
real and it is worth roughly +20 % of base asset a year against holding; whether any of it
survives a detector that must decide in real time is the only remaining question, and it is
now the whole question.

### The causal detector does not transfer, and that closes the gate (2026-09-09)

Everything the gate achieved rests on labels that look seven days forward. With the config
fixed outside the experiment (`mm=0.020 stop=0.9`), `scripts/analysis/lateral_detector.py`
replaces the oracle with rules that see only the past — so the only thing being chosen is the
detector. Three families, 63 variants, a grid fixed before running, all sharing one
confirmation filter (close the moment the condition fails, open only after `delay` quiet
days, since missing a range costs zero and opening in a trend does not): the **causal mirror**
of the oracle (closed if `|close_t/close_{t-k} - 1| >= move` for some `k <= look`), **box
containment**, and **Kaufman's efficiency ratio**. Volatility filtering was excluded on
purpose — already measured null. Protocol: rank on 2024+2025, 2023 held out.

| detector | 2024 | 2025 | **2023 (held out)** |
|---|---|---|---|
| `impulso m=0.10 k=5 d=7` | +44.6 % | +21.2 % | **−7.7 %** |
| `impulso m=0.10 k=10 d=0` | +21.8 % | +27.5 % | **−1.8 %** |
| `impulso m=0.10 k=10 d=7` | +13.3 % | +22.1 % | **−5.3 %** |
| `er n=30 t=0.3 d=3` | +18.2 % | −2.8 % | **−27.3 %** |
| `impulso m=0.10 k=5 d=3` | +26.1 % | +22.9 % | +0.1 % |
| **oracle ceiling** | **+33.7 %** | **+40.3 %** | **+21.8 %** |

All figures with local calibration, paired with the ceiling's. That pairing mattered enough to
check: the sweep first ran on global calibration, where 2024's ceiling is +9.7 % against
+33.7 % local, so comparing a global-calibration detector to a local-calibration ceiling was
unfair by 24 points. Matched, the verdict does not move — **on the held-out year every ranked
detector lands between −27.3 % and +0.1 % where the oracle earns +21.8 %.**

**The cause is in the precision column.** The detectors that look good on the fit years open
the gate on 60–76 % of bars with a precision of 60–66 %, and **20–28 % of their open bars sit
inside rising segments**. The oracle's precision is 100 %. This document already measured what
a rising segment costs an ungated bot: −60.9 %, −67.8 %, −1.5 %. Opening through a quarter of
the rallies is more than enough to consume a +21.8 % edge, and no amount of confirmation delay
fixes it — the delay that helps most on the fit years (`d=7`) is not the one that survives.

**And the deeper reason is one this document already reached by another route.** A causal
detector must decide "this is a range" at a moment when a range is indistinguishable from the
first days of a trend. The oracle's advantage was never *recognising* ranges — the trailing
mirror recognises them at 80–91 % recall. Its advantage is knowing that the impulse **ended**,
which is a fact about the next seven days. That is precisely the quantity the signal screen
measured and found unpredictable from every causal feature in these data (AUC 0.43–0.52 at
fixed horizons, price and flow alike). **The gate's value is forward information, and the
detector problem is the prediction problem wearing different clothes.**

**What the arc did establish, and it is not nothing.** Three of the four components verify:
holding by default removes the trend exposure in both directions (2023, 2024, 2025); a region
of the grid (`mm` 0.02–0.03, wide stop) transfers to a held-out year, landing at +23.5 %
against a grid best of +23.8 % and a grid median of +0.7 %; and that same config earns in
falling segments too (+11.4 %, +11.5 %, +6.3 %), so it is not range-specific. The fourth
component is the one that fails, and it is the one that turns a ceiling into a strategy.

**Honest bound on the negative.** 63 rules from three families is not proof that no rule
exists, and the grid was coarse. But it is the same wall the study hit from the label side,
reached independently from the detector side, and a rule that must resolve the next seven days
is asking for the thing already measured absent.

### Better rally precision does not buy a profitable detector (2026-09-09)

The first detector pass ranked 63 symmetric rules and found none transfers. Two objections
were right and both were tested. First, every rule was **symmetric in direction** — closing
on a 10 % fall exactly as on a 10 % rise — which throws away the falling-segment earnings for
no measured reason. Second, and this is the one that matters, all of them decide from a
**lagging statistic**: closing requires a 10 % move to complete over seven days, so the bot
trades through the entire opening of every rally. The owner's alternative is stateful — when
the market goes flat, record the range's ceiling, and close the gate the instant price crosses
it. No waiting for a move to complete.

Four directional families were added (up-only impulse, no-new-high, off-the-high, below-EMA)
and one stateful family (`rotura`: box or no-new-high entry, ceiling from the entry window,
optional floor so falls contribute nothing). Protocol tightened at the same time — the
hold-out year was **hardcoded to 2023** in the ranking, which silently admitted 2021 and 2022
into the fit once they were used; it is now an explicit `--holdout`.

**The stateful rule works, on the metric it was designed for.** It reaches the best agreement
in the whole bench: **78 % precision with 17 % of open bars inside rising segments**, against
55–66 % and 21–31 % for every lagging rule. And it does it while staying open 41 % of the time
at 64 % recall — the earlier families only reached 7–10 % rally contamination by collapsing to
6–18 % open time, where nothing is left to harvest. The compromise that looked inevitable is
broken; the mechanism does what the owner said it would.

**And it still does not make money out of sample.** Fitting on 2024+2025, holding out 2022 and
2019, with the floor on so the figure is range harvest alone:

| detector | 2024 | 2025 | **2022** | **2019** |
|---|---|---|---|---|
| `rotura caja n=20 s=0.15 +suelo` | +12.8 % | +16.6 % | **−1.1 %** | **−11.7 %** |
| `rotura caja n=30 s=0.15 +suelo` | +4.2 % | +13.3 % | **+0.1 %** | **−5.6 %** |
| oracle ceiling | +33.7 % | +40.3 % | +15.8 % | +12.5 % |

**So the finding is quantitative, not conceptual: 17 % residual rally exposure still costs
more than 83 % of a range harvest earns.** This document measured what a year's rising
segments cost an ungated bot — −60.9 %, −67.8 % — against a lateral harvest worth +12 % to
+40 % at the ceiling. The two are an order of magnitude apart, so a detector needs rally
precision far beyond 83 % before the arithmetic turns, and the best rule found reaches 83 %
only by construction on the exit side, not on the entry side.

**What is positive across every year, and why it does not count as this.** `bajo max n=30
p=0.05 d=3` — open only while price sits 5 % below its 30-day high, no floor — is positive in
all seven years measured: +24.0 (2019), +13.4 (2020), +29.8 (2021), +15.5 (2022), +6.2 (2023),
+9.2 (2024), +11.2 (2025). But it **beats its own oracle ceiling** in 2019 (+24.0 against
+12.5), 2020 (+13.4 against −3.9) and 2021 (+29.8 against +9.2), which is the signature
established earlier: a detector that exceeds the ceiling it approximates is doing something
else. It has no floor, so it trades the falls, and the years it beats the ceiling are the
years with large ones. In 2023 and 2024 — the years without a big fall to harvest — it
captures under a third of the ceiling. It is the falling-market edge in a new wrapper, not a
range detector.

**Where this leaves the gate, finally.** Three generations of detector — lagging symmetric,
directional, and stateful breakout — improve steadily on the metric that matters and none
clears the bar on held-out years. The last one improves rally precision by the largest margin
available in this rule space and still loses. Every remaining path either needs forward
information (measured absent) or reduces to trading falls (a strategy the owner set aside on
purpose). The gate stays closed as an avenue.

### The first continuous multi-year run that beats holding (2026-09-09)

The owner corrected a misreading: the falling-market edge was never set aside, it was being
held back until the detector was validated separately. With it back in, the gate is a causal
rule with **no floor** — it closes only on the rally side, so falls are traded. Scored on one
continuous run over 2023-01-01..2025-12-31, the study's closing window, where holding makes
**+383.56 %** in euros and the production grid's median is **−76.0 %** with 0 of 105 beating
hold:

| | base asset | EUR | ops |
|---|---|---|---|
| oracle ceiling (perfect labels) | +87.9 % | | |
| `alcista m=0.10 k=10 d=0` | **+42.0 %** | **+586.8 %** | 66 |
| `bajo max n=30 p=0.05 d=3` | **+27.6 %** | **+517.0 %** | 34 |
| `bajo max n=20 p=0.05 d=3` | +7.9 % | +421.7 % | 18 |
| production median (105 configs) | −76.0 % | | 57 |

**This is the first configuration in the study to beat holding over a continuous multi-year
run**, and the margin is not small — a swing of more than a hundred points against the
production median on the identical window, with a causal rule that predicts nothing.

**The per-year table overstated it badly, exactly as the harness rule says it would.**
`bajo max n=30 p=0.05 d=3` is positive in all seven years measured separately (+24.0, +13.4,
+29.8, +15.5, +6.2, +9.2, +11.2), and chaining those factors gives +172 %. The continuous run
gives **+27.6 %** — a 144-point overstatement, because each yearly run restarts the position
in January on whichever side is convenient. Segmented scoring is not a conservative
approximation; here it inflated the answer by a factor of six.

**What is and is not established.** Held out honestly: `bajo max n=30 p=0.05 d=3` earns
+24.0 % (2019), +13.4 % (2020) and +15.5 % (2022) in years that took no part in selecting it.
Not held out: this continuous window, which the rule was chosen after seeing. The mechanism is
also *not* what the gate set out to build — the detector beats its own oracle ceiling in 2019,
2020 and 2021, and captures only 31 % of it here, so what works is not range detection. It is
the much cruder **"do not trade while the price is making new highs"**, and a large part of
its edge is the falling-market behaviour this document measured years ago.

**And it remains long-biased.** In euros it loses 56 % in 2022 against holding's −62 %. It
accumulates more of the base asset in every year measured; it does not protect the euro
position, and nothing here changes that.

### Two continuous windows, seven years, one rule that beats holding (2026-09-09)

The owner pushed back on a reporting bias worth recording: `alcista m=0.10 k=10 d=0` was the
best detector in the 2023-2025 continuous run and this document had never assessed it on its
own. Two mistakes produced that. The whole `alcista` family had been diagnosed as
"trading the crashes, not detecting ranges" from the 2021 result, and that family-level
verdict was applied to every member without checking each. And `bajo max n=30 p=0.05 d=3` had
been preferred on a "positive in 7 of 7 years" headline, which separates two candidates by a
single year — a very thin basis, and it was not justified.

The full per-year record, with `bajo max n=30 p=0.05 d=3` beside it:

| year | `alcista m=0.10 k=10 d=0` | `bajo max n=30 p=0.05 d=3` | ceiling |
|---|---|---|---|
| 2019 | +9.8 % | +24.0 % | +12.5 % |
| 2020 | +21.9 % | +13.4 % | −3.9 % |
| 2021 | +32.5 % | +29.8 % | +9.2 % |
| 2022 | +25.1 % | +15.5 % | +15.8 % |
| 2023 | **−6.0 %** | +6.2 % | +21.8 % |
| 2024 | +17.9 % | +9.2 % | +33.7 % |
| 2025 | +19.2 % | +11.2 % | +40.3 % |

`alcista` also wins the diagnostics — 94 % recall against 62 %, and 24 % rally contamination
against 31 %. It detects better and admits fewer rally bars.

**The second continuous window settles it.** 2019-01-01..2022-12-31, position never reset,
holding **+376.38 %** in euros:

| | base asset | EUR | ops |
|---|---|---|---|
| `alcista m=0.10 k=10 d=0` | **+95.2 %** | **+829.8 %** | 88 |
| `alcista m=0.10 k=10 d=3` | +72.9 % | +723.5 % | 92 |
| `bajo max n=20 p=0.05 d=3` | +60.3 % | +663.8 % | 100 |
| `bajo max n=30 p=0.05 d=3` | +42.7 % | +579.9 % | 114 |
| oracle ceiling | +28.9 % | | |

**All four beat holding, in both windows, and the ordering is stable.** `alcista m=0.10 k=10
d=0` leads in 2019-2022 (+95.2 %) and in 2023-2025 (+42.0 %). Two continuous windows covering
2019-2025 with one config and one three-line causal rule, both positive against a benchmark
that returned +376 % and +384 %.

**Its one negative year did not matter.** `alcista` loses 6.0 % in 2023 scored alone, and wins
the 2023-2025 continuous run outright. That is the segmented-scoring defect from the other
side: a per-year table can condemn a rule as easily as it can flatter one, and only the
continuous run is the result.

**Adding confirmation hurts.** `d=3` costs 22 points against `d=0` in this window, so the
hypothesis that 2023 failed by flip-flopping in and out of choppy rallies is not supported.
The cause of the 2023 loss remains unmeasured.

**The mechanism, stated honestly, is not the one this line set out to build.** Every detector
here beats the oracle ceiling in 2019-2022, `alcista` by 66 points. A rule that beats the
ceiling it approximates is not approximating it. The oracle gate closes during falls; these
rules do not, and 2019-2022 contains the March 2020 crash, the May 2021 crash and the whole of
2022. **What works is "stop trading while the price is more than 10 % above any close of the
last ten days", and most of what it earns comes from trading the falls** — the conditional
edge this document measured on eight calendar years, now harnessed by a causal rule rather
than left to chance. Range detection remains unsolved and is not what is paying.

### The gate at production fidelity: a real repair, and no plateau anywhere (2026-09-10)

Two defects were removed at once. The engine resolves trailing once per 15-min bar — raising
the trail to the bar's high and testing it against that same bar's low — and the daily flag was
computed from a day's close and applied to that day from 00:00. `gate_live_fidelity.py` runs the
price path at **1 minute** (production's `SLEEPING_INTERVAL`), with ATR still Wilder over 15-min
bars projected forward and the calibration schedule built on the 15-min frame and remapped, so
the volatility view is the one production stores. On 2024, holding +134.61 % in euros:

| arm | 1 min | 15 min |
|---|---|---|
| no gate | −23.3 % | −42.4 % |
| daily flag on its own day (look-ahead) | +38.6 % | +24.5 % |
| daily flag lagged one day | −19.4 % | −27.9 % |
| **continuous: current price vs the last k completed daily closes** | **+3.4 %** | −4.9 % |

**Three things follow, and the third undoes the first.**

**The look-ahead in the daily flag is worth 58 points** at this fidelity, confirming
`gate_sensitivity.py` with a wider margin.

**What killed the rule was staleness, not causality.** The lagged and the continuous arm are
equally causal; the 23 points between them come from evaluating the rule when a bot could
evaluate it instead of carrying yesterday's close for 24 hours. Daily granularity was a harness
inheritance, never part of the hypothesis.

**Simulating at 15 minutes was costing the bot 19 points, ungated.** The intrabar artefact runs
*against* the bot: it manufactures exits the real bot would not take. Every negative measured at
15-min resolution in this document is understated by an unknown amount — this window and this
config put it at 19 points, while `execution_fidelity.py` found a median of +0.00 across 105
configs on a 2025 window, so the effect is neither uniform nor negligible and cannot be applied
as a blanket correction.

**Then the surface says the +3.4 % is noise.** `gate_families_live.py` converts every family
that ever ranked well to its continuous form — where the daily version used "today's close", the
continuous one uses "this minute's price" against the last `n` *completed* days — and runs 154
variants on 2024 at 1-min fidelity, one causal arm each, no look-ahead arm and no 15-min arm:

| family | n | median | best | above hold |
|---|---|---|---|---|
| `alcista` (up impulse) | 70 | −3.8 % | +29.6 % | 15 |
| `impulso` (symmetric) | 12 | −3.6 % | +22.7 % | 5 |
| `caja` | 18 | −0.4 % | +12.7 % | 5 |
| `er` (Kaufman) | 18 | −3.5 % | **+33.7 %** | 5 |
| `rotura` (stateful ceiling) | 6 | −3.9 % | +14.9 % | 2 |
| `bajo max` | 18 | −14.1 % | +2.3 % | 2 |
| `sin max` | 6 | −14.4 % | −9.7 % | 0 |
| `bajo ema` | 6 | −16.8 % | −13.9 % | 0 |
| **all** | **154** | **−5.3 %** | +33.7 % | **34 (22 %)** |

**The `alcista` surface has no plateau.** Its best point, `m=0.20 k=10` at +29.6 %, has four
orthogonal neighbours averaging **−8.4 %**: an isolated spike. The previous finding
`m=0.10 k=10` reproduces exactly at +3.4 % and sits in a mildly positive neighbourhood
(+1.9 % mean), but the surface's own dispersion is σ = 9.9, so +3.4 is not distinguishable from
zero. Across all 154, σ = 11.0 and the median is −5.3: **the maximum of 154 draws from that
distribution is roughly +22 to +34, which is what the ranking's head actually contains.** The
top two come from different families and both have poor neighbours. This is selection, not
signal.

**`bajo max n=30 p=0.05 d=3` is finished.** Positive in all seven years under the biased
harness; at production fidelity its family medians −14.1 % with 2 of 18 above hold.

**What is real, and it is not small.** Gating takes the bot from −23.3 % to a median of −5.3 %
in a year where the ungated grid was 0/105 against hold — an 18-point repair from rules that
predict nothing. The gate is a genuine filter. It is not a strategy: the median is still below
holding, and no member of it can be selected in advance, because the surface that would let you
choose one is noise.

### Still not established

- **Whether any data this study does not hold predicts.** Order book, trades tape, funding
  rates, cross-asset. The signal screen tested everything in the OHLCV archives, including the
  volume and trade-count columns nothing else had read, and found the null. That bounds these
  data, not all data.
- **Whether anything predicts on a pair that trades enough.** Every predictiveness and
  persistence result in this file was measured on XBTEUR, where the winners make 3–7
  operations. USDCEUR with a pair-scaled threshold makes 11–45, which is the first setting
  where the test could have power. Still the most informative unrun experiment, and now
  **deliberately deferred** — see the scope decision below.
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
  fixed in the engine. Measured again, and the size is worth recording: chaining `bajo max`'s
  seven yearly factors gives +172 % of base asset where the continuous run gives +27.6 %.
  Restarting the position each January inflated the answer six-fold.
- **The base asset, not euros, is the objective.** See the next section.
- **The current optimizer is closed as a research tool, and free per-level stops with it.** The deployed AUTO search on the fixed engine returns four different answers from four seeds; see "Free per-level stops do not converge". Any remaining question goes through the exhaustive sweep and the forward-percentile test.
- **Free per-level stops move the maximum and not the distribution.** On 2025 with the calibration schedule shared across groups, 600 random free-stop configs land at median +24.6 % against the 105 shared configs' +24.4 %, with the same quartiles and the same share beating hold; only the best moves (+73.5 % against +59.0 %), which is the order statistic of 600 draws from a superset. Whether that maximum survives forward is NOT settled. See "Immediate activation is closed; free stops move no distribution".
- **Per-side activation is closed.** Measured on the fixed engine and worse in the hypothesis direction; the per-side `min_margin` overrides stay in the engine as inert, tested fields.
- **Switching the config by regime is closed, operator-declared or otherwise.** With perfect regime labels the switched run is 18–19 points below the best fixed config; a regime's outcome is set by the side the bot holds when it begins, which no config can change. See "Switching the config by regime is closed".
- **The activation re-anchor stays, on both sides.** Removing it makes every cycle clean and turns the bot into one that sells once and waits for the price to come back; on 2024-10..2025-03 that is −22 % for all 105 configs with the whole window spent in cash. `reanchor_sell`/`reanchor_buy`/`reanchor_cap_at_entry` remain in the engine as inert switches.
- **A high AUC against a pivot-derived label is not evidence of a signal.** Pivots alternate, so "which leg am I on" is knowable from the trailing return and carries the label with it; any labelling scheme built from future extrema must be checked against a fixed-horizon forward label and a money test before it is believed. This cost one run that looked like a discovery. See "Reverse-engineering the strategy from ideal trades is closed".
- **There is no config recommendation, and the previous one was not skill.** `mm=0.05/0.9` loses 78 % of the base asset over 2023–2025 in four operations; it made one sell in the one year that fell. No config in the grid beats holding in 2023, 2024, or the three years continuous, under any re-anchor arm. See "Three years, and what the recommended config actually does".
- **The conditional edge is now separated cleanly, 8 years out of 8.** `mm=0` with the capped re-anchor beats holding in exactly the three calendar years 2018-2025 in which holding lost money, and loses in exactly the five in which it made money; time in cash is bimodal, 13-22 % in the winners against 77-99 % in the losers. It is a conditional instrument, not a strategy, and the condition is unpredictable by this document's own measurements. See "One config, eight years, only against hold".
- **A mask over the price series leaks unless the leg is reopened when it lifts.** A trailing stop that keeps tracking under a gate exits at a level anchored inside the mask, which is the oracle's label converted into money. Any future gated experiment must set `reset_on_unmask` and report the share of exits landing just after a lift; here that single switch was worth 159 points of apparent edge. See "Holding by default and trading only the ranges is closed".
- **The median is the honest estimator only where selection carries no information, and that must be checked per regime, not assumed.** Avenue 1 measured selection landing at percentile 50 forward over whole years, and this document then used the median everywhere. Inside lateral stretches the same test lands at percentile 93, so the median understates what a chosen config achieves there. Report the median *and* the forward percentile of a fitted pick; where they disagree, the percentile is the one that describes production, which runs one config.
- **Do not select a config by compounded return over a set of segments.** The compound is dominated by whichever segment was largest, so it selects for one lucky stretch; in 2024 it picked a config beating hold in 4 of 9 lateral stretches over one beating hold in 7 of 9. Select for a rate — segments won, or a per-segment median — and report both.
- **Selection needs segments, not years.** Fitting on half of one year's lateral stretches lands at percentile 55–63 in two of three years; fitting on the 18 stretches of two full years transfers to a held-out third. Four segments decide nothing. Report the base rate of segments won alongside any consistency claim — under the gate the median config wins 1 stretch in 8, which is what makes a 5-of-8 meaningful and a 9-of-9 suspicious.
- **A regime gate is worth exactly what its precision on rallies is worth, and that is a forward question.** Recognising a range is easy causally (80–91 % recall from a trailing rule); knowing an impulse has *ended* is not, and that is the whole of the oracle's advantage. Do not propose another regime filter without first stating what causal quantity resolves the next seven days, because the signal screen measured that none in these data does.
- **A detector that beats its own oracle ceiling is not approximating the oracle.** `bajo max` is positive in all seven years measured but exceeds the ceiling in 2019, 2020 and 2021 — the years with large falls — because it has no floor and trades them. Always report the ceiling beside the detector and treat any excess as a different strategy until decomposed.
- **A causal gate that stops trading while the price makes new highs beats holding over 2023-2025 continuous.** `bajo max n=30 p=0.05 d=3` returns +27.6 % of base asset (+517.0 % EUR against holding's +383.56 %) where the production median is −76.0 % and 0 of 105 configs beat hold; `alcista m=0.10 k=10 d=0` returns +42.0 %. The rule predicts nothing and is three lines. This is the first positive multi-year continuous result in the document — the window is not held out, but the same rule earns +24.0 %, +13.4 % and +15.5 % in 2019, 2020 and 2022, which are. See "The first continuous multi-year run that beats holding".
  **RETRACTED (2026-09-10)** for the same reason as the bullet above.
- **The best rule found is `alcista m=0.10 k=10 d=0`, and it wins both continuous windows.** Stop trading whenever today's close is 10 % or more above any close of the last ten days; trade otherwise, falls included. +95.2 % of base asset over 2019-2022 continuous and +42.0 % over 2023-2025, against holding's +376 % and +384 % in euros. It loses 6.0 % in 2023 scored alone and still wins the window containing it. Do not choose between candidates on a per-year win count. See "Two continuous windows, seven years, one rule that beats holding".
  **RETRACTED (2026-09-10).** Both windows were measured with a daily flag computed from a
  day's close and applied to that day from 00:00 — up to 24 h of look-ahead, worth a median
  of +42.9 points across the grid and +58 at 1-min fidelity. Shifted by one day, 0 of 70
  grid points beat holding. See "The gate at production fidelity".
- **XBTEUR only until the line is defined and built (owner, 2026-09-10).** The USDCEUR
  external-validity check remains the most informative *unrun* experiment in the document, but
  porting an undefined strategy to a second pair multiplies the search rather than validating
  it. Deferred, not dropped: revisit it once the gate's production semantics are settled.
- **Any rule that reads a bar-derived series must state which bar it is applied to, and be tested one bar later.** A daily flag applied to its own day is 24 h of look-ahead and it was worth more than the entire measured edge. This is the third defect of this shape in the study, after the pivot-label leak and the mask leak; the common signature is that the number is large, clean, and arrives before anyone has audited the information boundary.
- **A gate must be evaluated continuously, not on the grid of the series it reads.** The rule was written on daily closes and the harness therefore evaluated it once a day; that staleness alone was worth 23 points on 2024. The daily grid was never part of the hypothesis.
- **Simulating at 15 minutes runs against the bot, by an amount that is neither uniform nor known.** The intrabar artefact manufactures exits: 19 points on 2024 with `mm=0.020`, against a median of +0.00 across 105 configs on a 2025 window. Any strategy conclusion drawn at 15-min resolution is provisional until re-run at 1 min.
- **Gating is a real filter and not a strategy.** At production fidelity on 2024, 154 causal detectors median −5.3 % against the ungated bot's −23.3 % — an 18-point repair from rules that predict nothing — but still below holding, and the surface that would let you select one member is noise (σ = 11.0, best neighbours negative).
- **The closed-form rebalancing premium (`0.5*w(1-w)*sigma^2`) is a driftless result and must not be quoted for this asset.** Measured, it is negative in every rising window; the drift term dominates it by an order of magnitude. Any allocation rule that sells strength is making the bot's bet. See "The rebalancing premium does not survive the drift either".

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

**Eighteen avenues are now closed by measurement**, and none of them was a tuning question —
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
| Trading more often to dilute luck | confirmed, in the direction that hurts: over three years the configs making 100–784 operations converge *below* the passive ones (−93.0 % against −78.0 %), which is what a negative per-operation expectation looks like with more draws |
| Replacing the bot with a threshold-rebalanced constant mix | the rebalancing premium is negative in every rising window (−5.3 to −7.7 points at `w=0.5`) and worth +1.9 points in the one falling year; over three years −18.4 %, of which only 0.8 is fees and 17 is drift. Bounds the whole signal-free family at about +2 % a year, and only when the market falls |
| Overlaying entry timing on a monthly DCA | buying the whole contribution on day one wins in all three windows; splitting weekly or daily costs 0.5–2.9 points and dip limits 0.7–5.8, because every arm delays exposure against a positive drift. The fee lever is the only positive one measured anywhere in the study: +0.24 % over eight years moving from taker to maker |
| The bot's trailing entry as the DCA's buy rule | without a fall requirement it triggers in a median 0.8-6.8 hours and never once reaches a month's close untriggered, so it degenerates into buying on day one (±0.16 %); with one, 87 of 96 months enter below the day-one price and it still finishes 0.6-2.2 points behind, because the 7-9 months that never trigger buy after the rally. A high hit rate with a negative expectation |
| Immediate activation (`k_act = 0`) | the worst configuration the strategy has: 807 operations in 2025 for a median −95.3 % of base asset, 0/5 beating hold, in the one year holding lost money. The branch is monotone in the multiplier and only `k_act = 16` (15 ops) beats hold, which is the standing "trade less" result rather than anything about the branch |
| Holding by default and trading only confirmed ranges | the mechanism verifies on three years — masking the non-lateral bars takes the bot from 0/105 beating hold to 54/105 (2023) and 38/105 (2024) — and a config region (`mm` 0.02–0.03, wide stop) transfers to a held-out year at +23.5 % against a grid median of +0.7 %. But all of it rests on labels that see seven days forward. Replacing the oracle with 63 causal rules across three families, ranked on 2024+2025: on held-out 2023 every one lands between −27.3 % and +0.1 % against a +21.8 % ceiling, because their precision is 60–66 % and 20–28 % of their open bars fall inside rising segments. The detector problem is the prediction problem restated |
| Buying rally precision with recall, and with a stateful ceiling-break rule | measured on both. Tightening a lagging rule reaches 7–10 % rally contamination only by collapsing to 6–18 % open time, where nothing is left to harvest. A stateful rule that fixes the range ceiling on entry and exits the instant price crosses it reaches the bench's best agreement — 78 % precision, 17 % rally contamination, at 41 % open time — and with the floor on (range harvest alone) still returns −1.1 % and −11.7 % on held-out 2022 and 2019 against ceilings of +15.8 % and +12.5 %. Residual rally exposure of 17 % costs more than 83 % of the harvest earns |

The last two rows are closed **as range detection**, not as a gate. The same gating machinery,
pointed at rallies only and with the falls left tradeable, is the one thing in this document
that beats holding over a continuous multi-year run — see "Two continuous windows, seven years,
one rule that beats holding".

…and every one of them was measured on XBTEUR, where a config makes 3–7 trades a run. The
owner has decided (2026-09-10) that **nothing moves to another pair until the XBTEUR line is
fully defined and built**, so the USDCEUR external-validity check below is deferred rather than
dropped.

### Where the line stands (superseded — see "The gate at production fidelity")

**The table below was measured with a daily flag that looked 24 h forward, and is retracted.**
Kept as the record of what was believed before the fidelity run.

One configuration and one causal rule, fixed:

- `min_margin = 0.020`, `stop_pct = 0.9` on all five levels, `k_act` disabled.
- Gate `alcista m=0.10 k=10 d=0`: **stop trading whenever today's close is 10 % or more above
  any close of the last ten days; hold the asset while it is.** No confirmation delay, no
  floor, so falls are traded.

Scored as one continuous run at 0.40 % per leg, position never reset:

| window | rule (base asset) | rule (EUR) | hold (EUR) | production median |
|---|---|---|---|---|
| 2019-01..2022-12 | **+95.2 %** | +829.8 % | +376.4 % | — |
| 2023-01..2025-12 | **+42.0 %** | +586.8 % | +383.6 % | −76.0 %, 0/105 beat hold |

What that is and is not:

- **It is not range detection.** The rule beats its own oracle ceiling by 66 points in
  2019-2022. Most of the edge is the falling-market behaviour this document measured across
  eight calendar years, now captured by a causal rule instead of left to chance.
- **The rule's grid was fixed before running, but the choice among the four finalists saw
  2023-2025.** 2019-2022 was not used to select it; its per-year components were visible.
  Nothing in 2013-2018 has ever been shown to any detector.
- **Unmeasured before this could ship:** slippage (every fill is at exactly `stop_px`), the
  gate's evaluation semantics on a 15-minute engine driven by a daily-close rule, the
  sensitivity of `(m, k)` around 0.10/10, and what happens when the gate closes while the bot
  sits in cash.
- **The 2023 loss (−6.0 % scored alone) has no explanation.** Confirmation delay makes it
  worse, which refutes the flip-flop hypothesis.

What is left, in order:

1. **Run the gate on 2014-2018, which no detector has ever seen.** The archive starts in
   September 2013 and every detector result in this document begins in 2019, so five calendar
   years are a clean hold-out — the only one left. Then one continuous 2014-2025 run, position
   never reset, which is the number that settles whether this is a strategy or two lucky
   windows.
2. **Map the sensitivity of `(m, k)` around 0.10 / 10 days.** A plateau is a mechanism; a spike
   is a fitted parameter. This costs one sweep and decides how much of the rest is worth doing.
3. **Decompose where the money comes from, by segment class.** The claim "it is the falls" is
   inferred from the ceiling comparison, not measured directly. Report the rule's contribution
   from rising, falling and lateral bars, as `lateral_gate_oracle.py --decompose` already does
   for the oracle arm.
4. **Stress the two unmeasured costs.** Re-run the winner at taker fee and with a slippage term
   per exit, and report the breakeven slippage. The term is strictly negative and entirely
   absent from every figure above.
5. **Define the production semantics before writing any of it.** Which bar closes the gate on a
   15-minute engine; what happens to a position that is in cash when the gate closes; whether
   the gate can interrupt an owed exit (`stop_at` latched). These are design questions, not
   measurements, and the answers change the simulation.
6. **Implement the base-asset denomination** as a request flag (`objective: "EUR" | "BASE"`).
   Reporting only, until an objective compares across windows again — see that section for
   what it does and does not change.
7. **Decide whether shared `stop_pct` ships to production.** If it does, the deployed space
   becomes enumerable and Optuna/TPE/seeds/AUTO can be removed — a large simplification of
   `trading/optimizer/`. That decision needs its own spec.
8. **Unify the activation branches** into one two-dimensional space (defect 5). Strategy
   change: it touches `activation_distance` in both `trading/engine.py` and
   `trading/positions_manager.py`, and needs its own validation. Lower priority now that
   `k_act` is out of the experiments, but it is still the reason the profitable region only
   exists in one branch.
9. **Decide the schedule anchoring** (harness defects). Frame-anchored is faithful;
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
| `scripts/analysis/gate_live_fidelity.py` | **What is the gate worth when the bot is simulated as it actually runs?** 1-min price path (production's `SLEEPING_INTERVAL`), ATR still Wilder over 15-min bars projected forward, calibration schedule built on the 15-min frame and remapped. Four arms declared before running: no gate; the daily flag on its own day (look-ahead, kept only to size the bias); the daily flag lagged a day; and the rule evaluated at every bar against the last k *completed* daily closes. Also runs the 15-min path, so path resolution and flag freshness stop being confounded. Minutes per year. |
| `scripts/analysis/gate_families_live.py` | **Does any detector family survive at production fidelity, and is any of them a plateau?** Every family that ever ranked well, converted to its continuous form and run as a single causal arm on the 1-min path: 154 variants, the whole `alcista` surface among them. Reports the per-family distribution and the surface, because the head of a 154-row ranking on one year is an order statistic. Reuses the cached calibration schedule (`BOTC_POINT_CACHE`). |
| `scripts/analysis/gate_sensitivity.py` | **Is `alcista m=0.10 k=10` a mechanism or a fitted parameter?** 10 moves x 7 looks over each continuous window, with a `--lags` arm that shifts the daily flag; the L0 - L1 difference is the look-ahead bias, printed point by point. This is the harness that found the defect. |
| `scripts/analysis/lateral_detector.py` | **How much of the oracle gate survives a rule that only sees the past?** 63 causal detectors in three families (trailing impulse, box containment, Kaufman ER) with a shared confirmation filter, driving the gate for one fixed config, ranked on 2024+2025 with 2023 held out. Reports agreement with the oracle labels and splits the false positives by direction, since a false positive on a fall is not an error. `--local-top` re-runs the best few with calibration paired to the ceiling's. About 30 minutes for three years. |
| `scripts/analysis/lateral_gate_oracle.py` | **What is the ceiling on holding by default and trading only the ranges?** `force_hold_bars` over every non-lateral bar with impulse labels, four arms (no gate; gate with the anchor left live; gate with `reset_on_unmask`; plus a calibration that only sees its own range), 105 configs each, scored in base asset against holding. Decomposes each arm's best config by segment class and counts the exits landing within a day of a gate opening, which is what exposes a leaking mask. About 15 minutes for one year. |
| `scripts/analysis/capped_mm0_across_years.py` | **How does one configuration behave on intervals nobody chose?** A single config (`mm=0`, `stop=0.9`, capped re-anchor) over calendar years, scored in base asset against holding and nothing else, at two fee levels, with operation count and time in cash beside it. Deliberately has no grid and no median: it answers "what does this do", not "which is best". About 9 minutes per year, calibration-bound. |
| `scripts/analysis/kact_and_free_stops.py` | **The two gaps in the record: `k_act = 0` and the five free stops.** Runs the 105 shared-stop reference, the whole `k_act` branch broken out by multiplier, and a random sample of the free-stop space on one shared calibration schedule and window, scored in base asset. Reports the distribution and not only the best, because the best is where the selection effect hides. About 15 minutes for one year at 600 samples. |
| `scripts/analysis/dca_trailing_entry.py` | **Does the bot's trailing entry place the monthly buy better?** The live activation mechanism applied to the DCA's entry on 15-minute bars: run the low from the month's open, buy on a `bounce` x ATR reversal, optionally after a `fall` x ATR drop, market at the month's close otherwise. Reports median hours waited and the share of months entered below the day-one price beside the money, because the rule can win on both and lose on the third. |
| `scripts/analysis/dca_overlay.py` | **On a monthly DCA, what does automating the placement add?** Arms that contribute identical euros and differ only in where the buy lands (day one, split weekly/daily, dip limits with a month-end fallback, drawdown-scaled), scored as final value over euros contributed, on daily bars resampled from the 15-minute CSV. Seconds to run; `--fee` sizes the one positive lever. |
| `scripts/analysis/constant_mix_rebalance.py` | **What is a signal-free allocation rule worth here?** A constant-mix portfolio rebalanced when the weight leaves a band, checked every bar, swept over weight, band and fee. No engine, no configs, no calibration -- seconds to run. Reports against two benchmarks: holding (which a half-weight portfolio loses to by construction) and the same mix never rebalanced (which isolates the premium). |
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
