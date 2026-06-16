# Dynamic Pair Configuration — Design

**Date:** 2026-06-16
**Status:** Approved (brainstorming) — ready for implementation plan
**Roadmap:** V3 Phase 1 (regime filter moves to Phase 2)

## Goal

Make the pair-specific trading parameters currently fixed in `.env` —
`target_pct`, `hodl_pct`, `k_act`, `min_margin`, and the volatility-based stop
percentiles (`stop_pct_<level>`) — editable at runtime through both the HTTP API
and Telegram, with changes taking effect automatically on the next bot session,
no restart required.

A secondary, intentional cleanup ships with this work: `k_act` and `min_margin`
collapse from per-side (`buy`/`sell`) to a single value per pair, aligning the
live config with how the optimizer already treats them.

## Why

These parameters are the main strategy knobs. Today, tuning any of them requires
editing `.env` and restarting the `botc` container — slow, and it interrupts the
trading loop. Exposing them through the existing API + Telegram surfaces (the
same pattern as `/control/pause`) lets the operator retune live and lets the
optimizer's recommendations be applied without a redeploy.

The per-side collapse removes an existing inconsistency: the optimizer already
applies one `k_act`/`min_margin` to both sides and emits a single
`PAIR_K_ACT`/`PAIR_MIN_MARGIN`, while the live config still carries
`PAIR_SELL_/BUY_` variants and a per-side `SidePolicy` in the engine. `K_STOP`
stays per-side because it is *derived* from up/down-trend structural events.

## Decisions (resolved during brainstorming)

| Decision | Choice |
| --- | --- |
| Source of truth | **DB authoritative, seeded once from `.env`.** A new `pair_config` table. After seeding, `.env` edits to these params are ignored. |
| API shape | **Structured per-pair.** `GET /config`, `GET /config/{pair}`, `PATCH /config/{pair}` (partial, validated as a unit). |
| Telegram write | **Flat one-field-per-command.** `/config [pair]` to read, `/setconfig <pair> <field> <value>` to write. |
| Per-side collapse | **Full collapse including the engine.** Single `k_act`/`min_margin` everywhere; `K_STOP` stays per-side. Drop `PAIR_SELL_/BUY_` env vars. |
| `stop_pct` reload | **Recalc at the next bot session** via a per-pair dirty flag — not synchronous in the request, not waiting the full `PARAM_SESSIONS` cycle. |

## Architecture

### Data model — `pair_config` table

One row per pair, typed columns mapping 1:1 to the structured API:

```
pair (PK, str)
target_pct   (float)
hodl_pct     (float)
k_act        (float, nullable)
min_margin   (float)
stop_pct_ll  (float)
stop_pct_lv  (float)
stop_pct_mv  (float)
stop_pct_hv  (float)
stop_pct_hh  (float)
updated_at   (timestamp)
updated_by   (str, nullable)
```

A dedicated typed table (not the generic `BotControl` key/value store) because it
validates as a row, is self-documenting, and reads cleanly in SQL/Grafana.
Derived `K_STOP` is **not** stored — it stays computed by
`calculate_trading_parameters`.

Per CLAUDE.md, **both** the ORM model in `core/database.py` and a new Alembic
migration under `scripts/migrations/versions/` are updated together (CI builds
the schema from migrations).

### Source of truth — seed-once / DB-authoritative

At startup (`api/app.py` lifespan, after `validate_config` normalizes env into the
live dicts):

- For each pair, if a `pair_config` row exists → load its typed values into the
  live dicts (`TRADING_PARAMS`, `ASSET_ALLOCATION`, `STOP_PERCENTILES`),
  overriding the env-seeded values.
- If no row exists → insert one seeded from the validated env values.

So `.env` is the one-time seed; thereafter the DB wins. A pair added to `.env`
later (no row yet) is seeded on next startup; once a row exists, `.env` edits to
that pair are ignored.

### New module — `core/config_store.py`

Single owner of dynamic config. Consumers keep reading the existing module-level
dicts unchanged; the store keeps those dicts current.

- `load_or_seed()` — startup sync described above.
- `get_pair(pair)` / `get_all()` — typed reads for the API.
- `apply_patch(pair, fields, updated_by)` — validate → persist → update live dicts
  → flag dirty (if a `stop_pct` changed). Atomic; see Validation.

A module lock makes each multi-field patch atomic against the scheduler's reads.

### Live propagation

- `k_act`, `min_margin`, `target_pct`, `hodl_pct` are read live from the dicts
  every tick. Updating the dict in place is enough — effective next session.
- `stop_pct_<level>` only feeds `K_STOP` inside `calculate_trading_parameters`.
  `apply_patch` calls `runtime.mark_config_dirty(pair)` when any `stop_pct`
  changes.

**Runtime dirty flag** (`core/runtime.py`, under its existing lock):
`mark_config_dirty(pair)` and `pop_config_dirty(pair)` (returns + clears
atomically).

**Scheduler change** (`core/scheduler.py`, step 2 of the per-pair loop). The
recalc condition becomes:

```python
if (tick % PARAM_SESSIONS == 0) or runtime.pop_config_dirty(pair):
    calculate_trading_parameters(pair)
```

So a `stop_pct` change recomputes `K_STOP` at the next session
(~`SLEEPING_INTERVAL`), in the scheduler thread where calibration already runs —
never in the API request path.

### Concurrency

`apply_patch` holds the `config_store` lock for validate→persist→dict-update.
Per-key dict assignment is atomic under the GIL and the scheduler reads at tick
boundaries, so a patch landing mid-tick cannot tear a pair's config. Ordering is
**persist-then-update**: if the DB write fails, the in-memory dicts are left
untouched and the patch returns an error.

## API & Telegram surface

### HTTP — `api/routes/config.py`

Registered like the other routers; all endpoints require the `X-Api-Token`
header.

- `GET /config` → list of all pairs' typed config.
- `GET /config/{pair}` → one pair (404 if unknown).
- `PATCH /config/{pair}` → partial body; validates the pair as a unit, persists,
  updates live dicts, flags dirty if `stop_pct` changed; returns the new state.

Request/response models in `api/schemas.py` (`PairConfig`, `PairConfigPatch`).
All patch fields optional; `k_act` explicitly nullable (null = use the
`K_STOP + MIN_MARGIN` path).

```
PATCH /config/XBTEUR
{ "k_act": 2.0, "target_pct": 30, "stop_pct_hh": 0.95 }
```

### Telegram — `services/telegram/polling.py`

Thin REST callers, auth via existing `_check_auth`.

- `/config [pair]` → GET and pretty-print (all pairs, or one).
- `/setconfig <pair> <field> <value>` → one field per call → single-field PATCH.

Flat field names (side folded out): `target_pct`, `hodl_pct`, `k_act`,
`min_margin`, `stop_pct_ll`, `stop_pct_lv`, `stop_pct_mv`, `stop_pct_hv`,
`stop_pct_hh`. `/setconfig` with bad arity or an unknown field replies with usage
plus the valid field list; `k_act none` sends `null`. Both commands are added to
`/help` and `build_tg_app()`.

## The k_act/min_margin per-side collapse

Restructure `TRADING_PARAMS[pair]` so configured params are pair-level and only
derived `K_STOP` stays per-side:

```python
TRADING_PARAMS[pair] = {
    "K_ACT": float | None,
    "MIN_MARGIN": float,
    "K_STOP": {"buy": {level: k}, "sell": {level: k}},  # derived
}
```

Files touched:

- **`core/config.py`** — `_build_trading_params` reads only `PAIR_K_ACT` /
  `PAIR_MIN_MARGIN` (drop `PAIR_SELL_/BUY_` variants); no buy/sell split for the
  configured pair.
- **`core/validation.py`** — validate `k_act`/`min_margin` once per pair; extract
  per-pair normalize+validate into a reusable function (see Validation).
- **`trading/parameters_manager.py`** — `calculate_trading_parameters` writes
  `["K_STOP"]["buy"/"sell"]`; `get_k_stop` reads
  `TRADING_PARAMS[pair]["K_STOP"][side]`.
- **`trading/positions_manager.py`** — `calculate_activation_distance` reads
  pair-level `K_ACT`/`MIN_MARGIN`.
- **`trading/engine.py`** — remove `SidePolicy`; `EngineConfig` gains
  `k_act: float | None` and `min_margin: float`; `PairCalibration` keeps
  `k_stop_buy`/`k_stop_sell`; `activation_distance` uses `cfg.k_act`/
  `cfg.min_margin`.
- **`trading/optimizer/search.py`** — `_apply_candidate` builds `EngineConfig`
  with single values; `current_params` reads `TRADING_PARAMS[pair]["K_ACT"]`.
- **`trading/backtest.py`** + **`api/schemas.py`** — backtest request collapses
  any per-side `k_act`/`min_margin` to single fields; `EngineConfig` built
  accordingly.
- **`.env.example`**, **`docs/configuration.md`** — drop
  `PAIR_SELL_/BUY_K_ACT/MIN_MARGIN`; document `PAIR_K_ACT`/`PAIR_MIN_MARGIN` as
  single.

This is a breaking `.env` schema change (per-side variants removed), consistent
with the "seed once from env" model — the seed reads only the single keys.

## Validation

Extract the per-pair logic currently inline in `validate_pair_params` into a
reusable function:

```python
# core/validation.py
def normalize_pair_config(pair, raw: dict, *, all_targets: dict[str, float]) -> tuple[dict, list[str]]
```

It returns typed values + an error list, applying the existing rules in one place:

- `k_act`: float ≥ 0, or `None` (falls through to the `K_STOP + MIN_MARGIN`
  path).
- `min_margin`: float ≥ 0; **required** when `k_act` is `None`; ignored
  (normalized to `0.0`) when `k_act` is set.
- `target_pct`, `hodl_pct`: float in `[0, 100]`.
- `stop_pct_<level>`: float in `[0, 1]`, default `0.90` when unset.
- Cross-pair: sum of `target_pct` across all pairs ≤ 100, checked against the
  *proposed* state (other pairs' current targets + this patch).

Both startup (`validate_config`) and runtime (`config_store.apply_patch`) call
this same function — startup over all pairs, a patch over the one pair plus the
others' current targets for the sum check.

`apply_patch` is **atomic**: validate the merged pair first; on any error,
persist nothing, touch no dict, return the error list. Then persist, then update
dicts, then flag dirty.

### Error handling

- Unknown pair → 404.
- Validation failure (bad type, out of range, `target_pct` sum > 100, or
  `min_margin` missing when `k_act` is null) → 422 with a message Telegram
  surfaces verbatim.
- DB write failure → 500; dicts untouched.
- Telegram: bad arity / unknown field → usage + valid field list.

## Testing

Unit tests (`tests/unit/`, no external calls, monkeypatch DB/Kraken at import
site), targeting the 80% coverage gate:

- **`core/config_store`**: seed-when-absent vs load-when-present; `apply_patch`
  happy path updates dict + persists; invalid patch leaves dict + DB untouched;
  `stop_pct` change sets the dirty flag, others don't.
- **`core/validation`**: `normalize_pair_config` rules incl.
  `min_margin`-required-when-`k_act`-null, ranges, and cross-pair target-sum
  rejection.
- **`api/routes/config`**: GET all / GET pair / 404 unknown / PATCH success / 422
  invalid (monkeypatched store).
- **Telegram**: `/config` and `/setconfig` handlers — read formatting,
  single-field PATCH dispatch, bad arity / unknown field / `k_act none`, auth
  rejection.
- **Scheduler**: recalc fires when `pop_config_dirty` returns true even if the
  tick counter hasn't hit `PARAM_SESSIONS`.
- **Collapse regression**: `positions_manager`, `engine`, `optimizer`,
  `backtest` read single `k_act`/`min_margin`; update existing tests referencing
  the per-side structure.
- **Migration**: `pair_config` ORM model / Alembic migration parity.

## Out of scope

- The trend/chop regime filter (V3 Phase 2).
- Editing global (non-pair) settings (`SLEEPING_INTERVAL`, `PARAM_SESSIONS`,
  etc.) at runtime.
- Tolerance/clustering relaxations in the optimizer.
- A config change history/audit log beyond `updated_at`/`updated_by`.

## Design choices to record in CLAUDE.md

On implementation, add to the **Design choices** section:

- **Dynamic pair config is DB-authoritative, seeded once from `.env`.** Why a
  dedicated typed `pair_config` table over the generic `BotControl` store, and
  why `.env` becomes a one-time seed.
- **`stop_pct` changes recalc at the next session via a runtime dirty flag**, not
  synchronously in the request — keeps heavy calibration in the scheduler thread.
- **`k_act`/`min_margin` are single per pair** (`K_STOP` stays per-side because
  it is derived).
