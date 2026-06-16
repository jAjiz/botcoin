# Phase 1 – Dynamic Pair Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make per-pair trading parameters (`target_pct`, `hodl_pct`, `k_act`, `min_margin`, `stop_pct_<level>`) editable at runtime via the HTTP API and Telegram, persisted in PostgreSQL (DB-authoritative, seeded once from `.env`), with changes taking effect on the next bot session without a restart.

**Architecture:** A new `pair_config` table is the source of truth, seeded from `.env` on first boot. A new `core/config_store.py` owns load/seed and atomic patches, keeping the existing live module-level dicts (`TRADING_PARAMS`, `ASSET_ALLOCATION`, `STOP_PERCENTILES`) current so consumers are unchanged. `k_act`/`min_margin` are read live each tick; `stop_pct` changes set a per-pair dirty flag that triggers a `K_STOP` recalc on the next scheduler session. Shipping alongside is a cleanup that collapses `k_act`/`min_margin` from per-side to a single value per pair (`K_STOP` stays per-side because it is derived).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (sync) + Alembic, Pydantic v2, python-telegram-bot, pytest.

**Spec:** [`../specs/dynamic-pair-config-design.md`](../specs/dynamic-pair-config-design.md)

---

## File structure

**New files:**
- `core/config_store.py` — single owner of dynamic config: `load_or_seed`, `get_pair`, `get_all`, `apply_patch`, `ConfigValidationError`, `UnknownPairError`.
- `api/routes/config.py` — `GET /config`, `GET /config/{pair}`, `PATCH /config/{pair}`.
- `scripts/migrations/versions/20260616_01_pair_config.py` — Alembic migration for the `pair_config` table.
- `tests/unit/core/test_config_store.py`
- `tests/unit/api/test_config_route.py`

**Modified files:**
- `core/config.py` — collapse `_build_trading_params`; add `set_pair_config` / `get_pair_config` dict helpers.
- `core/validation.py` — extract `normalize_pair_config` + `target_sum_error`; rewrite `validate_pair_params`.
- `core/database.py` — add `PairConfig` model + DAL (`load_all_pair_config`, `upsert_pair_config`).
- `core/runtime.py` — per-pair config dirty flag.
- `core/scheduler.py` — recalc on dirty flag.
- `trading/engine.py` — remove `SidePolicy`; `EngineConfig` carries single `k_act`/`min_margin`.
- `trading/parameters_manager.py` — `K_STOP` nested under `["K_STOP"][side]`.
- `trading/positions_manager.py` — read pair-level `K_ACT`/`MIN_MARGIN`.
- `trading/optimizer/search.py` — build `EngineConfig` with single values; `_candidate_from_env` reads pair-level.
- `trading/backtest.py` — build `EngineConfig` with single values.
- `api/schemas.py` — `PairConfig`, `PairConfigPatch`.
- `api/app.py` — register config router; call `config_store.load_or_seed()` in lifespan.
- `services/telegram/polling.py` — `/config`, `/setconfig`, help text, handler registration.
- `.env.example`, `docs/configuration.md`, `CLAUDE.md`, `docs/ROADMAP.md` — docs + env cleanup.

**Modified tests (collapse regression):** `tests/unit/trading/test_engine.py`, `test_backtest.py`, `test_positions_manager.py`, `test_parameters_manager.py`, `tests/unit/optimizer/test_search.py`, `tests/unit/core/test_validation.py`, `tests/unit/services/test_telegram.py`, `tests/unit/core/test_scheduler.py`.

**Commands** (run from repo root; `PYTHONPATH=.` is required):
- Single test: `PYTHONPATH=. pytest tests/unit/path::test_name -v`
- Full unit suite + lint: `PYTHONPATH=. pytest tests/unit/ && python -m ruff check . && python -m ruff format --check .`

---

## Task 1: Collapse k_act/min_margin to a single per-pair value

This is a coordinated structural change to `TRADING_PARAMS[pair]`. It is committed as one unit because the dict shape changes across all consumers at once. Target shape:

```python
TRADING_PARAMS[pair] = {
    "K_ACT": float | None,
    "MIN_MARGIN": float,
    "K_STOP": {"buy": {level: k | None}, "sell": {level: k | None}},  # derived
}
```

**Files:**
- Modify: `core/config.py`, `core/validation.py`, `trading/parameters_manager.py`, `trading/positions_manager.py`, `trading/engine.py`, `trading/backtest.py`, `trading/optimizer/search.py`
- Test: `tests/unit/trading/test_engine.py`, `test_backtest.py`, `test_positions_manager.py`, `test_parameters_manager.py`, `tests/unit/optimizer/test_search.py`, `tests/unit/core/test_validation.py`

- [ ] **Step 1: Rewrite `_build_trading_params` and add dict helpers in `core/config.py`**

Replace the `_build_trading_params` function (currently `core/config.py:61-74`) with:

```python
# Trading params
def _build_trading_params() -> dict[str, dict[str, Any]]:
    params = {}
    for pair in PAIRS:
        params[pair] = {
            "K_ACT": os.getenv(f"{pair}_K_ACT"),
            "MIN_MARGIN": os.getenv(f"{pair}_MIN_MARGIN"),
            "K_STOP": {"buy": {}, "sell": {}},
        }
    return params
```

Then add these two helpers at the end of `core/config.py` (after `STOP_PERCENTILES = _build_percentiles()`):

```python
# Flat config view <-> live dicts. The "flat" dict uses the keys
# target_pct, hodl_pct, k_act, min_margin, stop_pct_ll..stop_pct_hh and is the
# representation shared by validation, the config store, and the API.
def set_pair_config(pair: str, typed: dict[str, Any]) -> None:
    TRADING_PARAMS[pair]["K_ACT"] = typed["k_act"]
    TRADING_PARAMS[pair]["MIN_MARGIN"] = typed["min_margin"]
    ASSET_ALLOCATION[pair]["TARGET_PCT"] = typed["target_pct"]
    ASSET_ALLOCATION[pair]["HODL_PCT"] = typed["hodl_pct"]
    for lvl in VOLATILITY_LEVELS:
        STOP_PERCENTILES[pair][lvl] = typed[f"stop_pct_{lvl.lower()}"]


def get_pair_config(pair: str) -> dict[str, Any]:
    flat = {
        "k_act": TRADING_PARAMS[pair]["K_ACT"],
        "min_margin": TRADING_PARAMS[pair]["MIN_MARGIN"],
        "target_pct": ASSET_ALLOCATION[pair]["TARGET_PCT"],
        "hodl_pct": ASSET_ALLOCATION[pair]["HODL_PCT"],
    }
    for lvl in VOLATILITY_LEVELS:
        flat[f"stop_pct_{lvl.lower()}"] = STOP_PERCENTILES[pair][lvl]
    return flat
```

- [ ] **Step 2: Add `normalize_pair_config` + `target_sum_error` and rewrite `validate_pair_params` in `core/validation.py`**

Replace the whole `validate_pair_params` function (currently `core/validation.py:93-159`) with:

```python
def normalize_pair_config(pair: str, raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate + normalize one pair's flat config.

    ``raw`` keys: k_act, min_margin, target_pct, hodl_pct, stop_pct_ll..stop_pct_hh
    (values may be strings, floats, or None). Returns (typed_flat, errors).
    Rules:
    - k_act: float >= 0, or None (falls through to K_STOP + MIN_MARGIN path).
    - min_margin: float >= 0; required when k_act is None; normalized to 0.0 when
      k_act is set.
    - target_pct, hodl_pct: float in [0, 100] (default 0.0 when unset).
    - stop_pct_<level>: float in [0, 1] (default STOP_PCT_DEFAULT when unset).
    The cross-pair target sum is checked separately by target_sum_error.
    """
    errors: list[str] = []
    out: dict[str, Any] = {}

    k_act = _parse_float(raw.get("k_act"), f"{pair}_K_ACT", errors, min_val=0)
    out["k_act"] = k_act

    mm_raw = raw.get("min_margin")
    if k_act is None:
        min_margin = _parse_float(mm_raw, f"{pair}_MIN_MARGIN", errors, min_val=0)
        if min_margin is None and mm_raw in (None, ""):
            errors.append(f"{pair}_MIN_MARGIN is required when K_ACT is not set")
        out["min_margin"] = min_margin
    else:
        parsed = _parse_float(mm_raw, f"{pair}_MIN_MARGIN", [], min_val=0)
        out["min_margin"] = parsed if parsed is not None else 0.0

    target = _parse_float(raw.get("target_pct"), f"{pair}_TARGET_PCT", errors, min_val=0, max_val=100)
    out["target_pct"] = target if target is not None else 0.0

    hodl = _parse_float(raw.get("hodl_pct"), f"{pair}_HODL_PCT", errors, min_val=0, max_val=100)
    out["hodl_pct"] = hodl if hodl is not None else 0.0

    for level in VOLATILITY_LEVELS:
        key = f"stop_pct_{level.lower()}"
        parsed = _parse_float(raw.get(key), f"{pair}_STOP_PCT_{level}", errors, min_val=0, max_val=1)
        out[key] = parsed if parsed is not None else STOP_PCT_DEFAULT

    return out, errors


def target_sum_error(targets: dict[str, float]) -> str | None:
    """Return an error string if the sum of target_pct across pairs exceeds 100."""
    total = sum(targets.values())
    if total > 100:
        return f"Sum of TARGET_PCT across all pairs must not exceed 100 (got {total:g})"
    return None


def validate_pair_params(errors: list[str]) -> None:
    """Validate and normalize per-pair trading parameters, writing typed values
    back into the live dicts. Shares normalize_pair_config with the runtime
    config store so startup and runtime apply identical rules."""
    typed_targets: dict[str, float] = {}
    for pair in PAIRS:
        raw = {
            "k_act": TRADING_PARAMS[pair]["K_ACT"],
            "min_margin": TRADING_PARAMS[pair]["MIN_MARGIN"],
            "target_pct": ASSET_ALLOCATION[pair]["TARGET_PCT"],
            "hodl_pct": ASSET_ALLOCATION[pair]["HODL_PCT"],
        }
        for lvl in VOLATILITY_LEVELS:
            raw[f"stop_pct_{lvl.lower()}"] = STOP_PERCENTILES[pair][lvl]

        typed, errs = normalize_pair_config(pair, raw)
        errors.extend(errs)
        config.set_pair_config(pair, typed)
        typed_targets[pair] = typed["target_pct"]

    err = target_sum_error(typed_targets)
    if err:
        errors.append(err)
```

Add `import core.config as config` near the top of `core/validation.py` (after the existing imports), and keep the existing `from core.config import (...)` block — it already imports `STOP_PCT_DEFAULT`, `VOLATILITY_LEVELS`, `TRADING_PARAMS`, `ASSET_ALLOCATION`, `STOP_PERCENTILES`, `PAIRS`. The `set_pair_config` helper is called via the `config` module alias to keep the write path in one place.

- [ ] **Step 3: Update `trading/parameters_manager.py` for the nested `K_STOP`**

In `calculate_trading_parameters`, replace the two K_STOP writes (currently `trading/parameters_manager.py:66-67`):

```python
    TRADING_PARAMS[pair]["sell"]["K_STOP"] = sell_k_stops
    TRADING_PARAMS[pair]["buy"]["K_STOP"] = buy_k_stops
```

with:

```python
    TRADING_PARAMS[pair]["K_STOP"] = {"sell": sell_k_stops, "buy": buy_k_stops}
```

In `build_calibration` (currently `trading/parameters_manager.py:102-103`), replace:

```python
        k_stop_buy=dict(TRADING_PARAMS[pair]["buy"].get("K_STOP") or {}),
        k_stop_sell=dict(TRADING_PARAMS[pair]["sell"].get("K_STOP") or {}),
```

with:

```python
        k_stop_buy=dict(TRADING_PARAMS[pair]["K_STOP"].get("buy") or {}),
        k_stop_sell=dict(TRADING_PARAMS[pair]["K_STOP"].get("sell") or {}),
```

In `get_k_stop`, replace the three `TRADING_PARAMS[pair][side]["K_STOP"]` / `TRADING_PARAMS[pair][op_side]["K_STOP"]` accesses (currently lines 123, 129, 138) so they index `["K_STOP"]` first:

```python
    k_stop = TRADING_PARAMS[pair]["K_STOP"][side].get(vol)
```
```python
    k_stop = TRADING_PARAMS[pair]["K_STOP"][op_side].get(vol)
```
```python
                k_stop = TRADING_PARAMS[pair]["K_STOP"][side].get(LEVELS[neighbor])
```

- [ ] **Step 4: Update `trading/positions_manager.py` to read pair-level params**

In `calculate_activation_distance` (currently `trading/positions_manager.py:47-57`), replace:

```python
    k_act = TRADING_PARAMS[pair][side]["K_ACT"]
```
with
```python
    k_act = TRADING_PARAMS[pair]["K_ACT"]
```

and replace:

```python
    min_margin = float(TRADING_PARAMS[pair][side]["MIN_MARGIN"])
```
with
```python
    min_margin = float(TRADING_PARAMS[pair]["MIN_MARGIN"])
```

- [ ] **Step 5: Collapse `EngineConfig` in `trading/engine.py`**

Remove the `SidePolicy` dataclass (currently `trading/engine.py:28-32`) and replace the `EngineConfig` dataclass (currently lines 34-41) with:

```python
@dataclass(frozen=True)
class EngineConfig:
    pair: str
    calibration: PairCalibration
    k_act: float | None
    min_margin: float
    atr_desv_limit: float
```

Replace `activation_distance` (currently lines 106-112) with:

```python
def activation_distance(cfg: EngineConfig, side: str, reference_price: float, atr_val: float) -> float:
    k_act = cfg.k_act
    if k_act is not None:
        return float(k_act) * atr_val
    k_stop = lookup_k_stop(cfg, side, atr_val) or 0.0
    return float(k_stop) * atr_val + (cfg.min_margin * reference_price)
```

- [ ] **Step 6: Update `trading/backtest.py` EngineConfig construction**

Change the import (currently `trading/backtest.py:15`) to drop `SidePolicy`:

```python
from trading.engine import EngineConfig, Operation, PairCalibration, simulate_operations
```

Replace the `side_buy` / `side_sell` / `cfg` block (currently lines 131-139) with:

```python
    cfg = EngineConfig(
        req.pair,
        calibration,
        k_act=_coerce_float(TRADING_PARAMS[req.pair].get("K_ACT")),
        min_margin=float(TRADING_PARAMS[req.pair].get("MIN_MARGIN") or 0.0),
        atr_desv_limit=ATR_DESV_LIMIT,
    )
```

- [ ] **Step 7: Update `trading/optimizer/search.py` EngineConfig + env read**

Change the import (currently `trading/optimizer/search.py:41`) to drop `SidePolicy`:

```python
from trading.engine import EngineConfig, PairCalibration, simulate_operations
```

Replace the last two lines of `_build_engine_config` (currently lines 255-256):

```python
    side = SidePolicy(k_act=cand.k_act, min_margin=cand.min_margin or 0.0)
    return EngineConfig(pair=pair, calibration=calibration, buy=side, sell=side, atr_desv_limit=atr_desv_limit)
```
with:
```python
    return EngineConfig(
        pair=pair,
        calibration=calibration,
        k_act=cand.k_act,
        min_margin=cand.min_margin or 0.0,
        atr_desv_limit=atr_desv_limit,
    )
```

In `_candidate_from_env` (currently lines 198-221), replace the two `TRADING_PARAMS[pair]["buy"].get(...)` reads (lines 204 and 212) with pair-level reads:

```python
        raw_k_act = TRADING_PARAMS[pair].get("K_ACT")
```
```python
        raw_mm = TRADING_PARAMS[pair].get("MIN_MARGIN", 0) or 0
```

- [ ] **Step 8: Update collapse-affected tests**

Apply these mechanical transformations across the listed test files (search each file for the old form and replace):

- `tests/unit/trading/test_engine.py`, `tests/unit/trading/test_backtest.py`, `tests/unit/optimizer/test_search.py`:
  - Remove any `SidePolicy` import and construction.
  - Replace `EngineConfig(..., buy=<X>, sell=<Y>, atr_desv_limit=Z)` with `EngineConfig(..., k_act=<X.k_act>, min_margin=<X.min_margin>, atr_desv_limit=Z)`. Where the test built a `SidePolicy(k_act=a, min_margin=m)` for both sides, pass `k_act=a, min_margin=m` directly.
- `tests/unit/trading/test_positions_manager.py`, `tests/unit/trading/test_parameters_manager.py`, `tests/unit/core/test_validation.py`:
  - Replace any `TRADING_PARAMS[pair]["buy"]["K_ACT"]` / `["sell"]["K_ACT"]` / `["buy"]["MIN_MARGIN"]` / `["sell"]["MIN_MARGIN"]` fixtures with the pair-level keys `TRADING_PARAMS[pair]["K_ACT"]` / `["MIN_MARGIN"]`.
  - Replace any `TRADING_PARAMS[pair]["buy"]["K_STOP"]` / `["sell"]["K_STOP"]` with `TRADING_PARAMS[pair]["K_STOP"]["buy"]` / `["sell"]`.
  - Any fixture dict literal of the old shape `{"buy": {"K_ACT": ...}, "sell": {...}}` becomes `{"K_ACT": ..., "MIN_MARGIN": ..., "K_STOP": {"buy": {...}, "sell": {...}}}`.
  - In `test_validation.py`, update assertions that referenced per-side k_act/min_margin to the pair-level keys, and keep the `TARGET_PCT` sum test (it now flows through `target_sum_error`).

- [ ] **Step 9: Run the affected suites to verify green**

Run: `PYTHONPATH=. pytest tests/unit/trading/ tests/unit/optimizer/ tests/unit/core/test_validation.py -v`
Expected: PASS (all collapse-affected tests green).

- [ ] **Step 10: Commit**

```bash
git add core/config.py core/validation.py trading/parameters_manager.py trading/positions_manager.py trading/engine.py trading/backtest.py trading/optimizer/search.py tests/unit/trading/ tests/unit/optimizer/ tests/unit/core/test_validation.py
git commit -m "refactor: collapse k_act/min_margin to a single per-pair value"
```

---

## Task 2: `pair_config` table (model + migration + DAL)

**Files:**
- Modify: `core/database.py`
- Create: `scripts/migrations/versions/20260616_01_pair_config.py`
- Test: `tests/unit/core/test_database.py`

- [ ] **Step 1: Add the `PairConfig` ORM model in `core/database.py`**

Add after the `BotControl` model (after `core/database.py:263`):

```python
class PairConfig(Base):
    """Per-pair dynamic trading configuration (DB-authoritative, seeded from env)."""

    __tablename__ = "pair_config"

    pair = Column(Text, primary_key=True, nullable=False)
    target_pct = Column(Numeric(6, 3), nullable=False, default=0)
    hodl_pct = Column(Numeric(6, 3), nullable=False, default=0)
    k_act = Column(Numeric(10, 4), nullable=True)
    min_margin = Column(Numeric(12, 8), nullable=False, default=0)
    stop_pct_ll = Column(Numeric(4, 3), nullable=False, default=0.90)
    stop_pct_lv = Column(Numeric(4, 3), nullable=False, default=0.90)
    stop_pct_mv = Column(Numeric(4, 3), nullable=False, default=0.90)
    stop_pct_hv = Column(Numeric(4, 3), nullable=False, default=0.90)
    stop_pct_hh = Column(Numeric(4, 3), nullable=False, default=0.90)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    updated_by = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("target_pct >= 0 AND target_pct <= 100", name="ck_pair_config_target_pct_range"),
        CheckConstraint("hodl_pct >= 0 AND hodl_pct <= 100", name="ck_pair_config_hodl_pct_range"),
        CheckConstraint("k_act IS NULL OR k_act >= 0", name="ck_pair_config_k_act_nonneg"),
        CheckConstraint("min_margin >= 0", name="ck_pair_config_min_margin_nonneg"),
        CheckConstraint("stop_pct_ll >= 0 AND stop_pct_ll <= 1", name="ck_pair_config_stop_ll_range"),
        CheckConstraint("stop_pct_lv >= 0 AND stop_pct_lv <= 1", name="ck_pair_config_stop_lv_range"),
        CheckConstraint("stop_pct_mv >= 0 AND stop_pct_mv <= 1", name="ck_pair_config_stop_mv_range"),
        CheckConstraint("stop_pct_hv >= 0 AND stop_pct_hv <= 1", name="ck_pair_config_stop_hv_range"),
        CheckConstraint("stop_pct_hh >= 0 AND stop_pct_hh <= 1", name="ck_pair_config_stop_hh_range"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair": self.pair,
            "target_pct": float(self.target_pct),
            "hodl_pct": float(self.hodl_pct),
            "k_act": float(self.k_act) if self.k_act is not None else None,
            "min_margin": float(self.min_margin),
            "stop_pct_ll": float(self.stop_pct_ll),
            "stop_pct_lv": float(self.stop_pct_lv),
            "stop_pct_mv": float(self.stop_pct_mv),
            "stop_pct_hv": float(self.stop_pct_hv),
            "stop_pct_hh": float(self.stop_pct_hh),
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }
```

- [ ] **Step 2: Add DAL functions in `core/database.py`**

Add after the Bot Control Operations section (after `set_bot_paused`, around `core/database.py:694`):

```python
# ============================================================================
# Pair Config Operations
# ============================================================================


def load_all_pair_config() -> dict[str, dict[str, Any]]:
    """Return {pair: pair_config_dict} for all stored pairs."""
    try:
        with get_session() as session:
            return {row.pair: row.to_dict() for row in session.query(PairConfig).all()}
    except Exception as e:
        logger.error(f"Error loading pair_config: {e}")
        return {}


def upsert_pair_config(pair: str, values: dict[str, Any], updated_by: str | None = None) -> None:
    """Insert or update one pair's config row. ``values`` is a flat typed dict
    with keys target_pct, hodl_pct, k_act, min_margin, stop_pct_ll..stop_pct_hh."""
    with get_session() as session:
        session.merge(PairConfig(pair=pair, updated_by=updated_by, **values))
    logger.debug(f"Saved pair_config for {pair}")
```

- [ ] **Step 3: Write the Alembic migration**

Create `scripts/migrations/versions/20260616_01_pair_config.py`:

```python
"""Phase 1 (V3): add pair_config table for dynamic per-pair configuration.

Revision ID: 20260616_01
Revises: 20260608_01
Create Date: 2026-06-16 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260616_01"
down_revision = "20260608_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pair_config",
        sa.Column("pair", sa.Text(), primary_key=True, nullable=False),
        sa.Column("target_pct", sa.Numeric(6, 3), nullable=False, server_default="0"),
        sa.Column("hodl_pct", sa.Numeric(6, 3), nullable=False, server_default="0"),
        sa.Column("k_act", sa.Numeric(10, 4), nullable=True),
        sa.Column("min_margin", sa.Numeric(12, 8), nullable=False, server_default="0"),
        sa.Column("stop_pct_ll", sa.Numeric(4, 3), nullable=False, server_default="0.90"),
        sa.Column("stop_pct_lv", sa.Numeric(4, 3), nullable=False, server_default="0.90"),
        sa.Column("stop_pct_mv", sa.Numeric(4, 3), nullable=False, server_default="0.90"),
        sa.Column("stop_pct_hv", sa.Numeric(4, 3), nullable=False, server_default="0.90"),
        sa.Column("stop_pct_hh", sa.Numeric(4, 3), nullable=False, server_default="0.90"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.CheckConstraint("target_pct >= 0 AND target_pct <= 100", name="ck_pair_config_target_pct_range"),
        sa.CheckConstraint("hodl_pct >= 0 AND hodl_pct <= 100", name="ck_pair_config_hodl_pct_range"),
        sa.CheckConstraint("k_act IS NULL OR k_act >= 0", name="ck_pair_config_k_act_nonneg"),
        sa.CheckConstraint("min_margin >= 0", name="ck_pair_config_min_margin_nonneg"),
        sa.CheckConstraint("stop_pct_ll >= 0 AND stop_pct_ll <= 1", name="ck_pair_config_stop_ll_range"),
        sa.CheckConstraint("stop_pct_lv >= 0 AND stop_pct_lv <= 1", name="ck_pair_config_stop_lv_range"),
        sa.CheckConstraint("stop_pct_mv >= 0 AND stop_pct_mv <= 1", name="ck_pair_config_stop_mv_range"),
        sa.CheckConstraint("stop_pct_hv >= 0 AND stop_pct_hv <= 1", name="ck_pair_config_stop_hv_range"),
        sa.CheckConstraint("stop_pct_hh >= 0 AND stop_pct_hh <= 1", name="ck_pair_config_stop_hh_range"),
    )


def downgrade() -> None:
    op.drop_table("pair_config")
```

- [ ] **Step 4: Write the failing test for the DAL round-trip**

Add to `tests/unit/core/test_database.py`:

```python
def test_pair_config_to_dict_round_trips_types():
    from decimal import Decimal
    from core.database import PairConfig

    row = PairConfig(
        pair="XBTEUR",
        target_pct=Decimal("30.000"),
        hodl_pct=Decimal("10.000"),
        k_act=Decimal("2.0000"),
        min_margin=Decimal("0.00100000"),
        stop_pct_ll=Decimal("0.900"),
        stop_pct_lv=Decimal("0.900"),
        stop_pct_mv=Decimal("0.900"),
        stop_pct_hv=Decimal("0.900"),
        stop_pct_hh=Decimal("0.950"),
    )
    d = row.to_dict()
    assert d["pair"] == "XBTEUR"
    assert d["target_pct"] == 30.0
    assert d["k_act"] == 2.0
    assert d["stop_pct_hh"] == 0.95
    assert isinstance(d["min_margin"], float)
```

- [ ] **Step 5: Run the test**

Run: `PYTHONPATH=. pytest tests/unit/core/test_database.py::test_pair_config_to_dict_round_trips_types -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add core/database.py scripts/migrations/versions/20260616_01_pair_config.py tests/unit/core/test_database.py
git commit -m "feat(db): add pair_config table, model and DAL"
```

---

## Task 3: Runtime config dirty flag + scheduler recalc

**Files:**
- Modify: `core/runtime.py`, `core/scheduler.py`
- Test: `tests/unit/core/test_runtime.py`, `tests/unit/core/test_scheduler.py`

- [ ] **Step 1: Write failing tests for the dirty flag in `tests/unit/core/test_runtime.py`**

```python
def test_config_dirty_flag_set_and_pop():
    import core.runtime as runtime

    assert runtime.pop_config_dirty("XBTEUR") is False
    runtime.mark_config_dirty("XBTEUR")
    assert runtime.pop_config_dirty("XBTEUR") is True
    # second pop returns False (cleared)
    assert runtime.pop_config_dirty("XBTEUR") is False


def test_config_dirty_flag_is_per_pair():
    import core.runtime as runtime

    runtime.mark_config_dirty("ETHEUR")
    assert runtime.pop_config_dirty("XBTEUR") is False
    assert runtime.pop_config_dirty("ETHEUR") is True
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. pytest tests/unit/core/test_runtime.py::test_config_dirty_flag_set_and_pop -v`
Expected: FAIL with `AttributeError: module 'core.runtime' has no attribute 'pop_config_dirty'`.

- [ ] **Step 3: Implement the dirty flag in `core/runtime.py`**

Add `"config_dirty": set()` to the `_shared_data` dict (after the `pair_calibration` entry), then add at the end of the module:

```python
def mark_config_dirty(pair: str) -> None:
    with _lock:
        _shared_data["config_dirty"].add(pair)


def pop_config_dirty(pair: str) -> bool:
    """Return True (and clear) if pair's config changed since the last check."""
    with _lock:
        if pair in _shared_data["config_dirty"]:
            _shared_data["config_dirty"].discard(pair)
            return True
        return False
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=. pytest tests/unit/core/test_runtime.py -v`
Expected: PASS.

- [ ] **Step 5: Update the scheduler recalc condition in `core/scheduler.py`**

Replace (currently `core/scheduler.py:93-94`):

```python
            if _session_count % PARAM_SESSIONS == 0:
                calculate_trading_parameters(pair)
```
with:
```python
            if _session_count % PARAM_SESSIONS == 0 or runtime.pop_config_dirty(pair):
                calculate_trading_parameters(pair)
```

- [ ] **Step 6: Write a failing test for the scheduler dirty-flag recalc in `tests/unit/core/test_scheduler.py`**

Match the file's existing monkeypatch style for `trading_session`. Add:

```python
def test_session_recalcs_params_when_config_dirty(monkeypatch):
    import core.scheduler as scheduler
    import core.runtime as runtime

    calls = []
    monkeypatch.setattr(scheduler, "calculate_trading_parameters", lambda pair, *a, **k: calls.append(pair))
    monkeypatch.setattr(scheduler.db, "create_session", lambda *_a, **_k: 1)
    monkeypatch.setattr(scheduler.db, "get_bot_paused", lambda: False)
    monkeypatch.setattr(scheduler.db, "finalize_session", lambda *a, **k: None)
    monkeypatch.setattr(scheduler.db, "load_trailing_state", lambda pair: None)
    monkeypatch.setattr(scheduler, "PAIRS", {"XBTEUR": {}})
    monkeypatch.setattr(scheduler, "get_balance", lambda: {"ZEUR": 100.0})
    monkeypatch.setattr(scheduler, "get_last_prices", lambda pairs: {"XBTEUR": 80000.0})
    monkeypatch.setattr(scheduler, "get_current_atr", lambda pair: 500.0)
    monkeypatch.setattr(scheduler, "get_volatility_level", lambda pair, atr: "MV")
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", False)
    monkeypatch.setattr(scheduler, "PARAM_SESSIONS", 720)
    # Force the counter off a multiple of PARAM_SESSIONS so only the dirty flag can trigger.
    monkeypatch.setattr(scheduler, "_session_count", 1)

    runtime.mark_config_dirty("XBTEUR")
    scheduler.trading_session()

    assert calls == ["XBTEUR"]
```

If the existing test module already monkeypatches a helper set for `trading_session`, reuse it instead of duplicating; the key assertion is that `calculate_trading_parameters` is called for a dirty pair when `_session_count` is not a multiple of `PARAM_SESSIONS`.

- [ ] **Step 7: Run scheduler + runtime tests**

Run: `PYTHONPATH=. pytest tests/unit/core/test_runtime.py tests/unit/core/test_scheduler.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add core/runtime.py core/scheduler.py tests/unit/core/test_runtime.py tests/unit/core/test_scheduler.py
git commit -m "feat(runtime): config dirty flag triggers K_STOP recalc next session"
```

---

## Task 4: `core/config_store.py`

**Files:**
- Create: `core/config_store.py`
- Test: `tests/unit/core/test_config_store.py`

- [ ] **Step 1: Write `core/config_store.py`**

```python
"""Single owner of dynamic per-pair configuration.

The live module-level dicts in ``core.config`` (TRADING_PARAMS, ASSET_ALLOCATION,
STOP_PERCENTILES) remain the read path for the trading loop. This module keeps
them current: it seeds them from the DB (or seeds the DB from them) at startup,
and applies validated runtime patches. A module lock makes each multi-field patch
atomic against the scheduler's reads.
"""

import threading
from typing import Any

import core.config as config
import core.database as db
import core.runtime as runtime
from core.config import PAIRS, VOLATILITY_LEVELS
from core.validation import normalize_pair_config, target_sum_error

_lock = threading.Lock()

# The flat config keys persisted in pair_config and exchanged with the API.
FLAT_KEYS = (
    "target_pct",
    "hodl_pct",
    "k_act",
    "min_margin",
    *[f"stop_pct_{lvl.lower()}" for lvl in VOLATILITY_LEVELS],
)


class UnknownPairError(Exception):
    """Raised when a config operation targets a pair not in PAIRS."""


class ConfigValidationError(Exception):
    """Raised when a patch fails validation. Carries the list of error strings."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _flat_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in FLAT_KEYS}


def get_pair(pair: str) -> dict[str, Any]:
    """Current typed flat config for one pair, read from the live dicts."""
    return config.get_pair_config(pair)


def get_all() -> dict[str, dict[str, Any]]:
    return {pair: config.get_pair_config(pair) for pair in PAIRS}


def load_or_seed() -> None:
    """Startup sync. For each pair: load the DB row into the live dicts if present,
    otherwise seed a row from the (already env-validated) live dict values."""
    rows = db.load_all_pair_config()
    for pair in PAIRS:
        if pair in rows:
            config.set_pair_config(pair, _flat_from_row(rows[pair]))
        else:
            db.upsert_pair_config(pair, config.get_pair_config(pair), updated_by="seed")


def apply_patch(pair: str, fields: dict[str, Any], updated_by: str | None = None) -> dict[str, Any]:
    """Validate, persist, and apply a partial config change for one pair.

    Returns the new typed flat config. Raises UnknownPairError for an unknown
    pair, ConfigValidationError on validation failure (nothing persisted or
    applied). Persist-then-apply: if the DB write fails the live dicts are
    untouched (the exception propagates)."""
    with _lock:
        if pair not in PAIRS:
            raise UnknownPairError(pair)

        current = config.get_pair_config(pair)
        merged = {**current, **fields}
        typed, errors = normalize_pair_config(pair, merged)

        targets = {p: config.get_pair_config(p)["target_pct"] for p in PAIRS if p != pair}
        targets[pair] = typed["target_pct"]
        sum_err = target_sum_error(targets)
        if sum_err:
            errors.append(sum_err)

        if errors:
            raise ConfigValidationError(errors)

        stop_changed = any(
            typed[f"stop_pct_{lvl.lower()}"] != current[f"stop_pct_{lvl.lower()}"] for lvl in VOLATILITY_LEVELS
        )

        db.upsert_pair_config(pair, typed, updated_by=updated_by)
        config.set_pair_config(pair, typed)
        if stop_changed:
            runtime.mark_config_dirty(pair)

        return typed
```

- [ ] **Step 2: Write tests in `tests/unit/core/test_config_store.py`**

```python
import pytest

import core.config as config
import core.config_store as config_store
import core.runtime as runtime


def _seed_dicts(monkeypatch, pairs):
    monkeypatch.setattr(config, "PAIRS", pairs)
    monkeypatch.setattr(config_store, "PAIRS", pairs)
    monkeypatch.setattr(config, "TRADING_PARAMS", {p: {"K_ACT": 2.0, "MIN_MARGIN": 0.0, "K_STOP": {"buy": {}, "sell": {}}} for p in pairs})
    monkeypatch.setattr(config, "ASSET_ALLOCATION", {p: {"TARGET_PCT": 30.0, "HODL_PCT": 10.0} for p in pairs})
    monkeypatch.setattr(config, "STOP_PERCENTILES", {p: {lvl: 0.90 for lvl in config.VOLATILITY_LEVELS} for p in pairs})


def test_load_or_seed_inserts_when_row_absent(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR"])
    monkeypatch.setattr(config_store.db, "load_all_pair_config", lambda: {})
    saved = {}
    monkeypatch.setattr(config_store.db, "upsert_pair_config", lambda pair, values, updated_by=None: saved.update({pair: values}))

    config_store.load_or_seed()

    assert saved["XBTEUR"]["k_act"] == 2.0
    assert saved["XBTEUR"]["target_pct"] == 30.0


def test_load_or_seed_loads_when_row_present(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR"])
    row = {
        "pair": "XBTEUR", "target_pct": 40.0, "hodl_pct": 5.0, "k_act": None,
        "min_margin": 0.002, "stop_pct_ll": 0.8, "stop_pct_lv": 0.8, "stop_pct_mv": 0.8,
        "stop_pct_hv": 0.8, "stop_pct_hh": 0.9, "updated_at": None, "updated_by": None,
    }
    monkeypatch.setattr(config_store.db, "load_all_pair_config", lambda: {"XBTEUR": row})
    monkeypatch.setattr(config_store.db, "upsert_pair_config", lambda *a, **k: pytest.fail("should not seed when row present"))

    config_store.load_or_seed()

    assert config.get_pair_config("XBTEUR")["target_pct"] == 40.0
    assert config.get_pair_config("XBTEUR")["k_act"] is None
    assert config.get_pair_config("XBTEUR")["min_margin"] == 0.002


def test_apply_patch_unknown_pair_raises(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR"])
    with pytest.raises(config_store.UnknownPairError):
        config_store.apply_patch("DOGEUR", {"target_pct": 1.0})


def test_apply_patch_updates_dict_and_persists(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR"])
    saved = {}
    monkeypatch.setattr(config_store.db, "upsert_pair_config", lambda pair, values, updated_by=None: saved.update({pair: values}))

    result = config_store.apply_patch("XBTEUR", {"target_pct": 25.0}, updated_by="api")

    assert result["target_pct"] == 25.0
    assert config.get_pair_config("XBTEUR")["target_pct"] == 25.0
    assert saved["XBTEUR"]["target_pct"] == 25.0


def test_apply_patch_invalid_leaves_dict_and_db_untouched(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR"])
    monkeypatch.setattr(config_store.db, "upsert_pair_config", lambda *a, **k: pytest.fail("must not persist on invalid"))

    with pytest.raises(config_store.ConfigValidationError):
        config_store.apply_patch("XBTEUR", {"target_pct": 150.0})

    assert config.get_pair_config("XBTEUR")["target_pct"] == 30.0  # unchanged


def test_apply_patch_target_sum_over_100_rejected(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR", "ETHEUR"])  # each currently 30
    monkeypatch.setattr(config_store.db, "upsert_pair_config", lambda *a, **k: pytest.fail("must not persist"))

    with pytest.raises(config_store.ConfigValidationError) as exc:
        config_store.apply_patch("XBTEUR", {"target_pct": 95.0})  # 95 + 30 > 100
    assert any("TARGET_PCT" in e for e in exc.value.errors)


def test_apply_patch_stop_pct_change_sets_dirty_flag(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR"])
    monkeypatch.setattr(config_store.db, "upsert_pair_config", lambda *a, **k: None)
    marked = []
    monkeypatch.setattr(config_store.runtime, "mark_config_dirty", lambda pair: marked.append(pair))

    config_store.apply_patch("XBTEUR", {"stop_pct_hh": 0.95})
    assert marked == ["XBTEUR"]


def test_apply_patch_non_stop_change_does_not_set_dirty_flag(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR"])
    monkeypatch.setattr(config_store.db, "upsert_pair_config", lambda *a, **k: None)
    marked = []
    monkeypatch.setattr(config_store.runtime, "mark_config_dirty", lambda pair: marked.append(pair))

    config_store.apply_patch("XBTEUR", {"target_pct": 20.0})
    assert marked == []


def test_apply_patch_k_act_null_requires_min_margin(monkeypatch):
    _seed_dicts(monkeypatch, ["XBTEUR"])
    # current min_margin is 0.0 and k_act is 2.0; setting k_act null with no margin -> still 0.0,
    # which is a valid float, so this checks the null path accepts an explicit margin.
    monkeypatch.setattr(config_store.db, "upsert_pair_config", lambda *a, **k: None)
    result = config_store.apply_patch("XBTEUR", {"k_act": None, "min_margin": 0.001})
    assert result["k_act"] is None
    assert result["min_margin"] == 0.001
```

- [ ] **Step 3: Run the tests**

Run: `PYTHONPATH=. pytest tests/unit/core/test_config_store.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add core/config_store.py tests/unit/core/test_config_store.py
git commit -m "feat(config): add config_store for load/seed and atomic patches"
```

---

## Task 5: HTTP API (schemas + route + wiring)

**Files:**
- Modify: `api/schemas.py`, `api/app.py`
- Create: `api/routes/config.py`
- Test: `tests/unit/api/test_config_route.py`

- [ ] **Step 1: Add Pydantic models to `api/schemas.py`**

Append:

```python
class PairConfig(BaseModel):
    pair: str
    target_pct: float
    hodl_pct: float
    k_act: float | None = None
    min_margin: float
    stop_pct_ll: float
    stop_pct_lv: float
    stop_pct_mv: float
    stop_pct_hv: float
    stop_pct_hh: float


class PairConfigPatch(BaseModel):
    """Partial update. Unset fields are ignored; an explicit null k_act switches
    the pair to the K_STOP + MIN_MARGIN activation path."""

    model_config = ConfigDict(extra="forbid")

    target_pct: float | None = Field(default=None, ge=0, le=100)
    hodl_pct: float | None = Field(default=None, ge=0, le=100)
    k_act: float | None = Field(default=None, ge=0)
    min_margin: float | None = Field(default=None, ge=0)
    stop_pct_ll: float | None = Field(default=None, ge=0, le=1)
    stop_pct_lv: float | None = Field(default=None, ge=0, le=1)
    stop_pct_mv: float | None = Field(default=None, ge=0, le=1)
    stop_pct_hv: float | None = Field(default=None, ge=0, le=1)
    stop_pct_hh: float | None = Field(default=None, ge=0, le=1)
```

Update the import at the top of `api/schemas.py` (currently `from pydantic import BaseModel, Field, model_validator`) to include `ConfigDict`:

```python
from pydantic import BaseModel, ConfigDict, Field, model_validator
```

- [ ] **Step 2: Create `api/routes/config.py`**

```python
from fastapi import APIRouter, HTTPException

import core.config_store as config_store
from api.schemas import PairConfig, PairConfigPatch
from core.config import PAIRS

router = APIRouter(tags=["config"])


def _to_model(pair: str, flat: dict) -> PairConfig:
    return PairConfig(pair=pair, **flat)


@router.get("/config", response_model=list[PairConfig])
def get_config() -> list[PairConfig]:
    return [_to_model(pair, flat) for pair, flat in config_store.get_all().items()]


@router.get("/config/{pair}", response_model=PairConfig)
def get_config_pair(pair: str) -> PairConfig:
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Unknown pair: {pair}")
    return _to_model(pair, config_store.get_pair(pair))


@router.patch("/config/{pair}", response_model=PairConfig)
def patch_config_pair(pair: str, patch: PairConfigPatch) -> PairConfig:
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Unknown pair: {pair}")
    fields = patch.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=422, detail="No fields to update")
    try:
        typed = config_store.apply_patch(pair, fields, updated_by="api")
    except config_store.ConfigValidationError as e:
        raise HTTPException(status_code=422, detail="; ".join(e.errors))
    return _to_model(pair, typed)
```

- [ ] **Step 3: Wire the router and startup seed in `api/app.py`**

Update the routes import (currently `api/app.py:15`):

```python
from api.routes import balance, config, control, market, positions, status
```

In `lifespan`, after `if not db.check_database_connection(): ...` and before building the scheduler, add:

```python
    config_store.load_or_seed()
```

Add the import near the top of `api/app.py` (with the other `core` imports):

```python
import core.config_store as config_store
```

Add `config` to the router registration tuple (currently `api/app.py:87`):

```python
for _r in (balance, config, control, market, positions, status, backtest_route, optimizer_route):
```

- [ ] **Step 4: Write tests in `tests/unit/api/test_config_route.py`**

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.config_store as config_store
from api.routes import config as config_route

_PAIRS = {"XBTEUR": {}, "ETHEUR": {}}
_FLAT = {
    "target_pct": 30.0, "hodl_pct": 10.0, "k_act": 2.0, "min_margin": 0.0,
    "stop_pct_ll": 0.9, "stop_pct_lv": 0.9, "stop_pct_mv": 0.9,
    "stop_pct_hv": 0.9, "stop_pct_hh": 0.9,
}


def _client(monkeypatch):
    monkeypatch.setattr(config_route, "PAIRS", _PAIRS)
    app = FastAPI()
    app.include_router(config_route.router)
    return TestClient(app)


def test_get_config_all(monkeypatch):
    monkeypatch.setattr(config_store, "get_all", lambda: {p: dict(_FLAT) for p in _PAIRS})
    body = _client(monkeypatch).get("/config").json()
    pairs = {item["pair"] for item in body}
    assert pairs == {"XBTEUR", "ETHEUR"}


def test_get_config_pair(monkeypatch):
    monkeypatch.setattr(config_store, "get_pair", lambda pair: dict(_FLAT))
    body = _client(monkeypatch).get("/config/XBTEUR").json()
    assert body["pair"] == "XBTEUR"
    assert body["k_act"] == 2.0


def test_get_config_unknown_pair_404(monkeypatch):
    assert _client(monkeypatch).get("/config/DOGEUR").status_code == 404


def test_patch_config_success(monkeypatch):
    captured = {}

    def _apply(pair, fields, updated_by=None):
        captured["pair"] = pair
        captured["fields"] = fields
        return {**_FLAT, **fields}

    monkeypatch.setattr(config_store, "apply_patch", _apply)
    resp = _client(monkeypatch).patch("/config/XBTEUR", json={"target_pct": 25.0})
    assert resp.status_code == 200
    assert resp.json()["target_pct"] == 25.0
    assert captured["fields"] == {"target_pct": 25.0}


def test_patch_config_explicit_null_k_act_is_forwarded(monkeypatch):
    captured = {}
    monkeypatch.setattr(config_store, "apply_patch", lambda pair, fields, updated_by=None: captured.update(fields) or {**_FLAT, "k_act": None, "min_margin": 0.001})
    resp = _client(monkeypatch).patch("/config/XBTEUR", json={"k_act": None, "min_margin": 0.001})
    assert resp.status_code == 200
    assert captured["k_act"] is None


def test_patch_config_validation_error_422(monkeypatch):
    def _apply(pair, fields, updated_by=None):
        raise config_store.ConfigValidationError(["XBTEUR_TARGET_PCT must be <= 100 (got 150)"])

    monkeypatch.setattr(config_store, "apply_patch", _apply)
    resp = _client(monkeypatch).patch("/config/XBTEUR", json={"target_pct": 99.0})
    assert resp.status_code == 422
    assert "TARGET_PCT" in resp.json()["detail"]


def test_patch_config_empty_body_422(monkeypatch):
    resp = _client(monkeypatch).patch("/config/XBTEUR", json={})
    assert resp.status_code == 422


def test_patch_config_unknown_pair_404(monkeypatch):
    assert _client(monkeypatch).patch("/config/DOGEUR", json={"target_pct": 1.0}).status_code == 404
```

- [ ] **Step 5: Run the route tests**

Run: `PYTHONPATH=. pytest tests/unit/api/test_config_route.py -v`
Expected: PASS.

- [ ] **Step 6: Add the auth-coverage assertion in `tests/unit/api/test_api.py`**

In `test_full_app_rejects_request_without_token`, add `/config` to the GET loop so the new router is covered by the auth check. Change the tuple (currently `tests/unit/api/test_api.py:98`):

```python
    for path in ("/balance", "/status", "/market", "/positions", "/config"):
```

- [ ] **Step 7: Run the API suite**

Run: `PYTHONPATH=. pytest tests/unit/api/ -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add api/schemas.py api/routes/config.py api/app.py tests/unit/api/test_config_route.py tests/unit/api/test_api.py
git commit -m "feat(api): config GET/PATCH endpoints + startup seed"
```

---

## Task 6: Telegram `/config` and `/setconfig`

**Files:**
- Modify: `services/telegram/polling.py`
- Test: `tests/unit/services/test_telegram.py`

- [ ] **Step 1: Add command handlers and field list in `services/telegram/polling.py`**

Add this module-level constant after the logger setup (after `services/telegram/polling.py:13`):

```python
_CONFIG_FIELDS = (
    "target_pct",
    "hodl_pct",
    "k_act",
    "min_margin",
    "stop_pct_ll",
    "stop_pct_lv",
    "stop_pct_mv",
    "stop_pct_hv",
    "stop_pct_hh",
)


def _format_pair_config(item: dict) -> str:
    k_act = item.get("k_act")
    k_act_str = "None" if k_act is None else f"{k_act:g}"
    return (
        f"━━━ {item['pair']} ━━━\n"
        f"target_pct: {item['target_pct']:g}\n"
        f"hodl_pct: {item['hodl_pct']:g}\n"
        f"k_act: {k_act_str}\n"
        f"min_margin: {item['min_margin']:g}\n"
        f"stop_pct: LL {item['stop_pct_ll']:g} | LV {item['stop_pct_lv']:g} | "
        f"MV {item['stop_pct_mv']:g} | HV {item['stop_pct_hv']:g} | HH {item['stop_pct_hh']:g}"
    )
```

Add the two command handlers (after `positions_command`, before `build_tg_app`):

```python
async def config_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    try:
        pair_filter = context.args[0].upper() if context.args else None
        if pair_filter and pair_filter not in PAIRS:
            await update.message.reply_text(f"❌ Unknown pair: {pair_filter}\nAvailable: {', '.join(PAIRS.keys())}")
            return

        url = f"/config/{pair_filter}" if pair_filter else "/config"
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        items = [data] if pair_filter else data
        msg = "⚙️ Pair Config:\n\n" + "\n\n".join(_format_pair_config(item) for item in items)
        await update.message.reply_text(msg)
    except Exception as e:
        logging.error(f"Error in config_command: {e}")
        await update.message.reply_text(f"❌ Error fetching config: {e}")


async def setconfig_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    usage = "Usage: /setconfig <PAIR> <field> <value>\nFields: " + ", ".join(_CONFIG_FIELDS)
    if len(context.args) != 3:
        await update.message.reply_text(usage)
        return

    pair = context.args[0].upper()
    field = context.args[1].lower()
    value = context.args[2]

    if pair not in PAIRS:
        await update.message.reply_text(f"❌ Unknown pair: {pair}\nAvailable: {', '.join(PAIRS.keys())}")
        return
    if field not in _CONFIG_FIELDS:
        await update.message.reply_text(f"❌ Unknown field: {field}\nFields: {', '.join(_CONFIG_FIELDS)}")
        return

    if value.lower() == "none":
        if field != "k_act":
            await update.message.reply_text("❌ Only k_act may be set to 'none'.")
            return
        body = {"k_act": None}
    else:
        body = {field: value}

    try:
        resp = await client.patch(f"/config/{pair}", json=body)
        if resp.status_code == 422:
            await update.message.reply_text(f"❌ Invalid: {resp.json().get('detail')}")
            return
        resp.raise_for_status()
        await update.message.reply_text(f"✅ {pair} {field} updated.")
    except Exception as e:
        logging.error(f"Error in setconfig_command: {e}")
        await update.message.reply_text(f"❌ Error updating config: {e}")
```

Register the handlers in `build_tg_app` (add before `return app`):

```python
    app.add_handler(CommandHandler("config", config_command))
    app.add_handler(CommandHandler("setconfig", setconfig_command))
```

Add the two commands to the `help_command` text (extend the command list and example):

```python
        "/config [pair] - Show pair configuration (all or specific pair)\n"
        "/setconfig <pair> <field> <value> - Update a config field\n"
```

- [ ] **Step 2: Write tests in `tests/unit/services/test_telegram.py`**

Reuse the existing `MockUpdate`, `MockContext`, `_mock_client`, `_mock_response` helpers. Add:

```python
_CONFIG_ITEM = {
    "pair": "XBTEUR", "target_pct": 30.0, "hodl_pct": 10.0, "k_act": 2.0, "min_margin": 0.0,
    "stop_pct_ll": 0.9, "stop_pct_lv": 0.9, "stop_pct_mv": 0.9, "stop_pct_hv": 0.9, "stop_pct_hh": 0.9,
}


@pytest.mark.asyncio
async def test_config_command_specific_pair(monkeypatch):
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")
    monkeypatch.setattr(polling, "PAIRS", {"XBTEUR": {}})
    monkeypatch.setattr(polling, "client", _mock_client(get=_mock_response(_CONFIG_ITEM)))

    update = MockUpdate()
    await polling.config_command(update, MockContext(args=["XBTEUR"]))

    assert "XBTEUR" in update.message.replies[0]
    assert "k_act: 2" in update.message.replies[0]


@pytest.mark.asyncio
async def test_setconfig_command_patches_single_field(monkeypatch):
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")
    monkeypatch.setattr(polling, "PAIRS", {"XBTEUR": {}})
    mock = _mock_client()
    mock.patch = __import__("unittest").mock.AsyncMock(return_value=_mock_response({**_CONFIG_ITEM, "target_pct": 25.0}))
    monkeypatch.setattr(polling, "client", mock)

    update = MockUpdate()
    await polling.setconfig_command(update, MockContext(args=["XBTEUR", "target_pct", "25"]))

    mock.patch.assert_called_once_with("/config/XBTEUR", json={"target_pct": "25"})
    assert "✅" in update.message.replies[0]


@pytest.mark.asyncio
async def test_setconfig_command_k_act_none_sends_null(monkeypatch):
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")
    monkeypatch.setattr(polling, "PAIRS", {"XBTEUR": {}})
    mock = _mock_client()
    mock.patch = __import__("unittest").mock.AsyncMock(return_value=_mock_response({**_CONFIG_ITEM, "k_act": None}))
    monkeypatch.setattr(polling, "client", mock)

    update = MockUpdate()
    await polling.setconfig_command(update, MockContext(args=["XBTEUR", "k_act", "none"]))

    mock.patch.assert_called_once_with("/config/XBTEUR", json={"k_act": None})


@pytest.mark.asyncio
async def test_setconfig_command_bad_arity_shows_usage(monkeypatch):
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")
    update = MockUpdate()
    await polling.setconfig_command(update, MockContext(args=["XBTEUR", "target_pct"]))
    assert "Usage:" in update.message.replies[0]


@pytest.mark.asyncio
async def test_setconfig_command_unknown_field(monkeypatch):
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")
    monkeypatch.setattr(polling, "PAIRS", {"XBTEUR": {}})
    update = MockUpdate()
    await polling.setconfig_command(update, MockContext(args=["XBTEUR", "nonsense", "1"]))
    assert "Unknown field" in update.message.replies[0]


@pytest.mark.asyncio
async def test_setconfig_command_none_rejected_for_non_k_act(monkeypatch):
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")
    monkeypatch.setattr(polling, "PAIRS", {"XBTEUR": {}})
    update = MockUpdate()
    await polling.setconfig_command(update, MockContext(args=["XBTEUR", "target_pct", "none"]))
    assert "Only k_act" in update.message.replies[0]


@pytest.mark.asyncio
async def test_config_command_rejects_unauthorized(monkeypatch):
    monkeypatch.setattr(polling, "TELEGRAM_USER_ID", "123456789")
    update = MockUpdate(user_id=999)
    await polling.config_command(update, MockContext())
    assert update.message.replies == []
```

- [ ] **Step 3: Run the telegram tests**

Run: `PYTHONPATH=. pytest tests/unit/services/test_telegram.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add services/telegram/polling.py tests/unit/services/test_telegram.py
git commit -m "feat(telegram): /config and /setconfig commands"
```

---

## Task 7: Docs, `.env`, and full verification

**Files:**
- Modify: `.env.example`, `docs/configuration.md`, `CLAUDE.md`, `docs/ROADMAP.md`

- [ ] **Step 1: Drop per-side activation vars from `.env.example`**

Remove any lines matching `*_SELL_K_ACT`, `*_BUY_K_ACT`, `*_SELL_MIN_MARGIN`, `*_BUY_MIN_MARGIN`. Ensure each pair documents only single `<PAIR>_K_ACT` and `<PAIR>_MIN_MARGIN`. (Search the file for `SELL_K_ACT` / `BUY_K_ACT` / `SELL_MIN_MARGIN` / `BUY_MIN_MARGIN` and delete those lines; keep `<PAIR>_K_ACT=` and `<PAIR>_MIN_MARGIN=`.)

- [ ] **Step 2: Update `docs/configuration.md`**

In the per-pair parameters section, remove the `PAIR_SELL_K_ACT` / `PAIR_BUY_K_ACT` / `PAIR_SELL_MIN_MARGIN` / `PAIR_BUY_MIN_MARGIN` variants and state that `K_ACT` and `MIN_MARGIN` are single per pair. Add a short subsection: these params (plus `TARGET_PCT`, `HODL_PCT`, `STOP_PCT_<level>`) are DB-authoritative once seeded — editable at runtime via `GET/PATCH /config/{pair}` and Telegram `/config` + `/setconfig`; `.env` seeds them on first boot only.

- [ ] **Step 3: Update `CLAUDE.md`**

- In the Database section, change "Six ORM models" to "Seven ORM models" and add `PairConfig` to the list; document the `pair_config` table (DB-authoritative per-pair config, seeded once from `.env`).
- In the Configuration section, replace the `PAIR_K_ACT (or PAIR_SELL_K_ACT / PAIR_BUY_K_ACT)` line with a single `PAIR_K_ACT` (note per-side variants removed) and likewise `PAIR_MIN_MARGIN`.
- Add three entries to the **Design choices** section:
  - **Dynamic pair config is DB-authoritative, seeded once from `.env`** (a dedicated typed `pair_config` table over the generic `BotControl` store; `.env` becomes a one-time seed).
  - **`stop_pct` changes recalc at the next session via a runtime dirty flag** (heavy calibration stays in the scheduler thread, never the request path).
  - **`k_act`/`min_margin` are single per pair** (`K_STOP` stays per-side because it is derived).

- [ ] **Step 4: Tick the ROADMAP Phase 1 checkboxes**

In `docs/ROADMAP.md` Phase 1, mark the scope checkboxes complete (`- [x]`) as each lands; at minimum mark them all complete here at the end.

- [ ] **Step 5: Run the full unit suite + lint + format**

Run: `PYTHONPATH=. pytest tests/unit/ && python -m ruff check . && python -m ruff format --check .`
Expected: PASS, coverage ≥ 80%.

- [ ] **Step 6: Run the migration locally to confirm it applies**

Run: `docker compose -f docker-compose.test.yml run --rm test alembic upgrade head`
Expected: migration `20260616_01` applies cleanly; `pair_config` table created.

- [ ] **Step 7: Commit**

```bash
git add .env.example docs/configuration.md CLAUDE.md docs/ROADMAP.md
git commit -m "docs: dynamic pair config + single k_act/min_margin"
```

---

## Self-review notes (verification against the spec)

- **Source of truth (DB-authoritative, seed once):** Task 2 (table) + Task 4 (`load_or_seed`) + Task 5 Step 3 (lifespan call).
- **Structured per-pair API:** Task 5 (`GET /config`, `GET /config/{pair}`, `PATCH /config/{pair}`).
- **Telegram flat one-field-per-command:** Task 6.
- **Full per-side collapse incl. engine:** Task 1 (engine, config, validation, parameters_manager, positions_manager, backtest, optimizer).
- **`stop_pct` recalc next session via dirty flag:** Task 3.
- **Atomic validation, cross-pair target sum, persist-then-apply:** Task 1 (`normalize_pair_config` / `target_sum_error`) + Task 4 (`apply_patch`).
- **Error handling (404 / 422 / persist-untouched):** Task 5 (route) + Task 4 (store) + Task 6 (telegram).
- **Testing across store/validation/API/telegram/scheduler/collapse + migration parity:** Tasks 1–6 tests + Task 7 Steps 5–6.
