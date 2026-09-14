# Optimizer simplification — enumerate, don't sample

**Status:** implemented on `feature/optimizer-calibration`.
**Depends on:** [`optimizer-validation-design.md`](optimizer-validation-design.md), whose
measurements are the entire justification for this change.

## The decision this rests on

One `stop_pct` shared by the five volatility levels, not five searched independently.

That was settled during the validation study and is recorded there under *Decisions taken*.
Its reason was identifiability: at the operation counts these configs produce (3–7 a year on
XBTEUR), a run exercises two or three volatility levels, so the remaining levels' `stop_pct`
values are never exercised, the search fills them with noise, and the selection then treats
that noise as signal.

That was an argument until it was measured. The last thing the study ran was the deployed
`AUTO` search with all five levels free — XBTEUR 2025, fee 0.4 %, `stop_pcts` 0.0–1.0 step
0.1, `min_margin` fixed:

> `converged: False` — **0 of 4 seeds agreed on a config** after escalating to 3 000 trials
> each (12 000 evaluations, 4 832 s).

Four independent TPE searches returned four different answers. The five-level space is not a
place where the search finds a better config; it is a place where the search finds no stable
config at all.

## What follows mechanically

With one shared `stop_pct` the space is a **small product**: `|min_margin grid| × |stop grid|`
plus `|k_act grid| × |stop grid|`. On the default grid that is 21 × 5 = **105 candidates**.

A space of 105 points does not need a sampler. `enumerate_candidates` walks both branches in
a deterministic order, `run_optimize` evaluates each exactly once, and the result is ranked by
the same `robust_pnl = min(train_pnl, test_pnl)` as before.

Everything that existed to cope with a space too large to enumerate is therefore gone:

| Removed | Why it existed | Why it no longer does |
|---|---|---|
| Optuna, TPE sampler | search a space larger than the budget | the budget now exceeds the space |
| `seed`, `n_trials` | make a sample reproducible and bounded | there is no sample; the enumeration is exhaustive and deterministic |
| `AUTO`, `n_seeds`, `min_agree`, `trial_step`, `max_trials` | check that independently seeded samplers agree | no sampler, so nothing to disagree |
| Warm-started escalation | reuse a study across trial budgets | no study |
| 2-process branch pool | run the two branches concurrently | 105 evaluations, run inline |

`trading/optimizer/search.py` goes from 724 to ~580 lines, and from 368 to 260 measured
statements. `requirements.txt` drops `optuna`.

**The property gained is worth as much as the code removed:** an identical request now returns
an identical ranking, always. Under TPE it did not — that was the whole subject of the AUTO
machinery, and the answer it gave was that it did not converge.

## What deliberately did *not* change

- **The ranking metric.** Still `min(train_pnl, test_pnl)` in euros. Base-asset figures are
  reported beside every candidate but never ranked on; see defect 6 in the validation spec for
  the measurement showing the base-asset ranking would be *worse*, not better.
- **The scoring path.** `_build_eval_context`, `_evaluate`, `_split_scores_from_single_run`,
  the calibration schedule and CONTINUE-only splitting are untouched. This is a change of
  search strategy, not of simulation.
- **`min_ops` / `min_test_ops`.** The sampler pruned trials below these counts; enumeration
  filters candidates, with the same meaning.
- **The database.** `ck_opt_jobs_mode_valid` still admits `AUTO`, so historical rows stay
  valid and **no migration is needed**. `OptimizerRequest.mode` still accepts `AUTO` for the
  same reason — a stored AUTO job must read back. The rejection lives at the route (`422`),
  which is where `search_space`'s requirement already lived.
- **Retired result fields.** `n_trials_run`, `converged`, `seeds_used`, `n_seeds_agreed`
  survive on `OptimizerResultResponse` as optional, so a stored AUTO result still renders.
  A current job reports `n_candidates` instead.

## Search-space defaults

`SearchSpace` previously required all three grids with no defaults, because grid coarseness
was an experiment input. The study is over, so the grids are now settled and each default
carries its finding:

| Grid | Default | Why |
|---|---|---|
| `min_margin` | 0.00–0.20 step 0.01 | the extremes must stay visible — below 0.01 a config sits at percentile 0–2 in every period, above 0.15 it barely trades — while the only characterised region, 0.04–0.07, is in the middle |
| `stop_pcts` | 0.5–0.9 step 0.1 | below 0.5 the stop sits under the median retracement already observed; 1.0 is a sample maximum set by a single observation, not a percentile (defect 4) |
| `k_act` | `null` (off) | won none of the study's twelve hold-out fits; defect 5 explains why structurally |

The default is filled in **at the route**, not on the model, so the stored request records the
grid the job actually ran — the self-documenting property the original per-request design
existed for.

## What this does not claim

It does not make the optimizer find configs that work. The validation study closed ten avenues
and its verdict stands: in-sample selection lands at percentile 50 of the forward distribution.
This change makes the search honest and cheap about a space we already know the shape of. It
removes a source of false precision — a non-reproducible ranking presented as a recommendation
— and nothing more.
