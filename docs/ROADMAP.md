# BoTCoin V3 – Roadmap

This is the active roadmap for BoTCoin. It continues from the **closed V2 milestone**, whose goal — evolving BoTCoin into a production-grade backend service with professional persistence, observability, and testability — was achieved across Phases 0–10. The frozen V2 record lives at [`v2/ROADMAP.md`](v2/ROADMAP.md).

V3's overarching theme is still being formalized. This roadmap is currently seeded with the two items that were scoped under V2 but never built. Other work already in progress (e.g. Dynamic Configuration) will be folded in here once V3's direction is settled.

---

## 📋 Table of Contents

- [Phased Roadmap](#-phased-roadmap)
  - [Phase 1 – Strategy Refinement: Trend/Chop Regime Filter](#phase-1--strategy-refinement-trendchop-regime-filter)
- [Appendix: Deferred Phases](#-appendix-deferred-phases)
  - [Auto-Lookback Window for K_STOP Calibration](#auto-lookback-window-for-k_stop-calibration)

---

## 🚀 Phased Roadmap

---

### Phase 1 – Strategy Refinement: Trend/Chop Regime Filter

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
