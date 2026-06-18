# BoTCoin V3 – Roadmap

This is the active roadmap for BoTCoin. It continues from the **closed V2 milestone**, whose goal — evolving BoTCoin into a production-grade backend service with professional persistence, observability, and testability — was achieved across Phases 0–10. The frozen V2 record lives at [`v2/ROADMAP.md`](v2/ROADMAP.md).

V3's overarching theme is still being formalized. This roadmap is currently seeded with the items scoped under V2 but never built, plus Dynamic Pair Configuration as Phase 1.

---

## 📋 Table of Contents

- [Phased Roadmap](#-phased-roadmap)
  - [Phase 1 – Dynamic Pair Configuration](#phase-1--dynamic-pair-configuration)
  - [Phase 2 – Strategy Refinement: Trend/Chop Regime Filter](#phase-2--strategy-refinement-trendchop-regime-filter)
- [Appendix: Deferred Phases](#-appendix-deferred-phases)
  - [Auto-Lookback Window for K_STOP Calibration](#auto-lookback-window-for-k_stop-calibration)

---

## 🚀 Phased Roadmap

---

### Phase 1 – Dynamic Pair Configuration

**Goal:** Make the pair-specific trading parameters currently fixed in `.env` —
`target_pct`, `hodl_pct`, `k_act`, `min_margin`, and the volatility-based stop
percentiles (`stop_pct_<level>`) — editable at runtime through both the HTTP API
and Telegram, with changes taking effect automatically on the next bot session,
no restart required. Ships with a cleanup: `k_act`/`min_margin` collapse from
per-side to a single value per pair (`K_STOP` stays per-side, as it is derived).

**Why:** These parameters are the main strategy knobs. Tuning any of them today
requires editing `.env` and restarting `botc`, interrupting the trading loop.
Exposing them through the existing API + Telegram surfaces (mirroring
`/control/pause`) lets the operator retune live and apply optimizer
recommendations without a redeploy. The per-side collapse removes an existing
inconsistency — the optimizer already treats `k_act`/`min_margin` as single
values.

**Scope:** Full design at
[`specs/dynamic-pair-config-design.md`](specs/dynamic-pair-config-design.md).

- [x] `pair_config` table (ORM model + Alembic migration); DB-authoritative,
      seeded once from `.env`
- [x] `core/config_store.py` — load/seed at startup, typed reads, atomic
      `apply_patch` that updates the live dicts
- [x] Per-pair dirty flag in `core/runtime.py`; `core/scheduler.py` recalcs
      `K_STOP` at the next session when a `stop_pct` changes
- [x] `GET /config`, `GET /config/{pair}`, `PATCH /config/{pair}`
      (`api/routes/config.py` + schemas)
- [x] Telegram `/config [pair]` and `/setconfig <pair> <field> <value>`
- [x] Collapse `k_act`/`min_margin` to single per-pair across config, validation,
      `positions_manager`, `engine`, `optimizer`, `backtest`; drop
      `PAIR_SELL_/BUY_` env vars; update `.env.example` + `docs/configuration.md`
- [x] Reusable `normalize_pair_config` shared by startup validation and runtime
      patches (incl. cross-pair `target_pct` sum check)
- [x] Unit tests across store, validation, API, Telegram, scheduler, and the
      collapse regression (80% gate)

**Success criteria:** An operator can query and modify any pair-specific
parameter via the API and Telegram; changes persist in `pair_config` and take
effect on the next session without a restart; `.env` seeds config only on first
boot; `k_act`/`min_margin` are single per pair everywhere.

---

### Phase 2 – Strategy Refinement: Trend/Chop Regime Filter

**Goal:** Add a Choppiness Index–based regime classifier that gates new position entries during sideways markets while leaving the trailing-stop exit logic untouched. The filter reuses the existing OHLC + ATR pipeline, introduces no new external dependencies, and ships in two stages — observation first, enforcement second — so behavior changes are validated against live data before being enabled.

**Why:** The current ATR-based volatility classification measures move *magnitude* but not move *efficiency* — a low-vol trend and a low-vol chop receive identical K_STOP values and are treated identically. Trailing-stop strategies bleed in sideways markets through repeated false-reversal entries (each clipped by fees and slippage), so adding a regime filter that gates new entries during chop addresses the strategy's known weak case without altering the exit logic. The Choppiness Index reuses the existing ATR pipeline and fits the project's percentile-calibration style.

**Scope:**

#### Stage A — Observation
- [ ] Add `get_trend_regime` in `trading/market_analyzer.py` that computes the Choppiness Index from the existing ATR pipeline and `ohlc_data`
- [ ] Classify the regime into `TREND` / `MIXED` / `CHOP` using empirically derived percentile boundaries from each pair's own historical OHLC (matching the K_STOP calibration style)
- [ ] Apply hysteresis at boundary transitions to prevent oscillation (separate thresholds for entering vs leaving each regime, with a configurable dead band)
- [ ] Expose the current per-pair regime via `core/runtime.py` and surface it through `GET /market` and the Telegram `/market` command
- [ ] Log every regime transition (info-level) so a long enough observation window can be reviewed before flipping enforcement on
- [ ] Unit tests for CI computation, regime classification, hysteresis, and the runtime/API surface

#### Stage B — Enforcement
- [ ] Extend the activation precondition in `trading/positions_manager.py` so `create_position` (or activation inside `tick_position`) is gated by `regime != CHOP`
- [ ] Active positions are deliberately unaffected — the trailing stop remains the sole exit policy across regime flips
- [ ] Add a `TRADE_ON_CHOP` per-pair env flag (default `false` once Stage B ships) so the gate can be toggled per pair without redeploy
- [ ] Unit tests covering: no activation during CHOP, normal activation in TREND/MIXED, and that existing open positions continue to tick and exit unchanged across regime flips

#### Calibration & docs
- [ ] Document threshold values and their derivation method in `trading-strategy.md`
- [ ] Revalidate thresholds via `POST /optimizer/jobs` (delivered in V2 Phase 10) comparing gated vs ungated PnL over historical data; record the chosen values back into `trading-strategy.md`

**Dependencies:**

- Stage A is independent and can ship immediately — it is purely observational.
- Stage B can ship using percentile-derived thresholds; principled threshold optimization uses the backtest/optimizer endpoints delivered in V2 Phase 10.

**Success criteria:** The bot publishes a per-pair regime label through the API and Telegram. With enforcement enabled, no new positions activate while the regime is `CHOP`, and regime transitions are smoothed by hysteresis (no flicker within the configured dead band). Existing open positions are unaffected by regime flips and continue to exit only via the trailing stop. Threshold values are documented in `trading-strategy.md` with a traceable derivation (percentile or backtest-derived).

---

## 📎 Appendix: Deferred Phases

Phases that were designed but deferred due to prerequisite data or changed priorities.

---

### Auto-Lookback Window for K_STOP Calibration

**Goal:** Replace full-history K_STOP calibration with a data-driven lookback window selected per pair via a K_STOP stability sweep, so the percentile-based stop sizing reflects the current volatility regime rather than the entire price history.

**Why deferred:** The plateau heuristic requires a meaningful history range to produce a stable signal. With only ~28 days of OHLC at the time of design, no candidate window could be reliably distinguished from another — the sweep would always fall back to the longest feasible window, adding cost with no benefit. Revisit once ~60+ days of OHLC are available (~mid-July 2026).

**Design notes:** Sweep candidates `[30d, 45d, 60d, 90d, 120d, 180d, 240d, 365d]`; pick the smallest window whose K_STOP agrees within 10% relative tolerance with the next two longer windows. Uses a fixed neutral P90 percentile for the sweep, independent of per-pair `PAIR_STOP_PCT_*` values, to isolate window-driven variance from percentile-driven variance. Builds on the calibration cache delivered in V2 Phase 10.

---

*This roadmap will be updated as V3's direction is formalized and phases complete.*
