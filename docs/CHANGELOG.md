# Changelog

All notable changes to BoTCoin are documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

---

## [2.10.1] – V2 Milestone Closed

The V2 milestone — evolving BoTCoin into a production-grade backend service — is complete (Phases 0–10). This release is a documentation-only reorganization that freezes the V2 record and opens the V3 roadmap.

### Changed
- Archived the V2 roadmap and all phase execution plans under `docs/v2/` (`docs/v2/ROADMAP.md`, `docs/v2/plans/`). The V2 roadmap was reviewed and closed (consistency fixes; "carried forward to V3" note).
- Renamed `docs/plan/` → `docs/plans/`; `docs/plans/` now holds plans for the active roadmap.
- Repointed `README.md` links to the new documentation paths.

### Added
- Fresh V3 roadmap at `docs/ROADMAP.md`, seeded with the two items scoped under V2 but never built: Trend/Chop Regime Filter (V3 Phase 1, formerly V2 Phase 11) and the deferred Auto-Lookback Window for K_STOP Calibration.

### Removed
- Retired the unused GitHub Copilot environment from Phase 0 (`.github/agents/`, `.github/instructions/`, `.github/skills/`). AI-assisted development now uses Claude Code, documented in `CLAUDE.md`.

---

## [2.10.0] – Phase 10: Trading Tools Integration — Backtest + Optimizer

### Added
- **Simulation Engine:** A pure, config-driven engine for backtesting and optimizer simulations (`trading/engine.py`).
- **Optimization Suite:** Asynchronous Optuna TPE search with a job management system (`JobStore`), including REST endpoints for execution and status tracking.
- **Calibration Cache:** Persistent structural events and ATR percentiles in `core/runtime.py`.
- **Infrastructure & Notifications:** Pydantic schemas, Telegram notifications, and pinned dependencies.

### Changed
- Refactored `trading/backtest.py` into a pure library entry point to reuse calibration cache and engine logic.
- Renamed optimizer search script and replaced exhaustive grid constants with Optuna TPE search.
- Streamlined `market_analyzer.py` to act as a library component only.

### Removed
- CLI entry points from research scripts (backtest, optimizer, analyzer).
- Exhaustive candidate constants and legacy printing helpers in the optimizer package.

---

## [2.9.0] – Phase 9: Project Documentation & Portfolio Framing

### Added
- **Documentation Suite:** Comprehensive guides for configuration, trading strategy, and operations (local dev/production deploy) moved to `docs/`.
- **Portfolio Revamp:** Overhauled `README.md` as an engineering cover letter including Mermaid architecture diagrams, key decisions table, and a PostgreSQL ERD.

### Changed
- Updated `ROADMAP.md` and `CLAUDE.md` with new documentation links and expanded design choices.
- Corrected repository URLs in CI workflows.

---

## [2.8.0] – Phase 8: Observability — Grafana Dashboard

### Added
- **Observability Suite:** Grafana 11 integration with a pre-built "BoTC Overview" dashboard provisioned from the repo.
- **Session Logging:** New `sessions` table capturing start/end times, completion status, balance snapshots, and log lines per scheduler tick.
- **Database Security:** Dedicated read-only Postgres role (`grafana_reader`) for Grafana access.

---

## [2.7.0] – Phase 7: CI/CD Pipeline

### Added
- `docker-compose.prod.yml` — production override replacing `build:` with `image: ghcr.io/jajiz/botc:${IMAGE_TAG:-main}` for `botc` and `telegram`.
- `.github/workflows/ci.yml` — unified five-job pipeline (Lint, Unit tests, Integration tests, Build & Push, Deploy).

### Removed
- `.github/workflows/deploy.yml` — superseded by `ci.yml`.

---

## [2.6.0] – Phase 6: Code Quality — Linting & Type Safety

### Added
- `pyproject.toml` as the single source of truth for `ruff`, `pytest`, and coverage configuration.
- `ruff` pinned in `requirements-dev.txt`.
- Full argument and return-type annotations across all major packages.
- Helper functions (`_safe_call`, `_to_decimal_required`) to collapse repeated patterns.

### Changed
- Normalized logging conventions; updated Type Hints from `Optional/List` to the modern `| None` / `list[]` syntax.
- Replaced inline `TODO` comments with GitHub issue links.

### Removed
- `pytest.ini` and `.coveragerc` (moved to `pyproject.toml`).

---

## [2.5.0] – Phase 5: REST API Layer — FastAPI

### Added
- **FastAPI REST Layer:** Multi-endpoint API (`market`, `positions`, `balance`, etc.) with Pydantic v2 schemas and `API_SECRET_TOKEN` protection.
- **Independent Telegram Service:** Dedicated FastAPI service for Telegram polling, notifications, and command handling via `httpx`.
- **Asynchronous Scheduling:** Integration of `AsyncIOScheduler` with FastAPI lifespan hooks using a dedicated thread pool.

### Changed
- Refactored Telegram handlers to delegate all requests to the main bot API via HTTP.
- Replaced the original blocking scheduler with the async implementation.

---

## [2.4.0] – Phase 4: Professional Persistence — PostgreSQL

### Added
- `core/database.py` — DAL with four ORM models (`OHLCData`, `ClosedPosition`, `TrailingState`, `BotControl`) and module-level functions.
- Alembic migration `20260414_01_phase4_initial_schema.py` creating all four tables with appropriate indexes.
- `scripts/load_legacy_data.py` — one-time migration script importing existing CSV/JSON data into PostgreSQL.
- Full `postgres` service in `docker-compose.yml` with health check and named volume.

### Removed
- Flat-file persistence (JSON for state, CSV for OHLC history) from the production path.

---

## [2.3.0] – Phase 3: Testing Strategy

### Added
- `tests/unit/` — pure-logic test suite for `core/`, `trading/`, and `exchange/`.
- `tests/integration/` — optional live-connectivity tests gated by environment variables.
- `docker-compose.test.yml` with a `test` service for running the suite inside Docker.
- `pytest.ini` with markers (`unit`, `integration`) and an 80% coverage gate.

---

## [2.2.1] – Phase 2.1: API Efficiency

### Added
- Module-level rate limiter in `exchange/kraken.py` enforcing 1-second minimum between public API calls.
- `TELEGRAM_ENABLED` flag in `.env` to disable Telegram initialization without touching call sites.

### Changed
- ATR calculation now uses the penultimate candle (`iloc[-2]`) instead of the latest to avoid incomplete-candle bias.

### Removed
- Blanket `time.sleep(1)` calls from the main loop and error handlers.

---

## [2.2.0] – Phase 2: Managed Execution — APScheduler

### Added
- `apscheduler` as a runtime dependency.
- `IntervalTrigger` job replacing the `while True` loop, with `max_instances=1`.
- `SIGTERM`/`SIGINT` handlers calling `scheduler.shutdown(wait=True)` for graceful shutdown.
- `call_with_retry` for read-only API calls (balance, prices, ATR).

### Removed
- Unmanaged `while True` loop from `main.py`.

---

## [2.1.0] – Phase 1: Infrastructure — Docker

### Added
- `Dockerfile` using a Python 3.12 slim base image.
- `docker-compose.yml` with `botc` and `postgres` service stubs.
- `.dockerignore` excluding `.env`, `__pycache__`, `data/`, and test artefacts.
- `.env.example` documenting all supported environment variables.

---

## [2.0.0] – Phase 0: AI-Assisted Development Environment

### Added
- `.github/agents/`, `.github/instructions/`, `.github/skills/` infrastructure.
- Awesome-Copilot agents, instructions, and skills for architectural consistency and accelerated delivery.
- `CLAUDE.md` documenting project architecture, design choices, and collaboration conventions for AI-assisted development.

---

## [1.0.0] – BoTCoin V1

Functional, modular trading bot with clean separation of concerns across `core/`, `exchange/`, `trading/`, and `services/`. Ran an unmanaged `while True` loop, persisted state and history as JSON and CSV flat files, and exposed a Telegram interface directly in-process. No linter, no CI test gate, no structured persistence layer. V2 was started to address these gaps; V1 changes are not retroactively documented.
