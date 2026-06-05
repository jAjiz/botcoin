# Changelog

All notable changes to BoTCoin are documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

---

## [2.10.0] – Phase 10: Trading Tools Integration — Backtest + Optimizer

### Added
- `trading/engine.py` — pure config-as-argument simulation engine (`PairCalibration`, `EngineConfig`, `SidePolicy`, `Operation`, `simulate_operations`) reading no module-level globals; includes a port of `reanchor_activation_price` into the simulation
- Structural-events calibration cache in `core/runtime.py` (`update_pair_calibration` / `get_pair_calibration`): per-pair structural events + ATR percentiles, dual-written by `calculate_trading_parameters` without changing its calculation logic
- `OptimizerJob` ORM model and `optimizer_jobs` table (Alembic migration `20260602_02`) with DAL helpers (create / complete / fail / get / list / cleanup-orphaned); orphan-cleanup hook on FastAPI lifespan startup
- `trading/optimizer/` package: `search.py` (Optuna TPE `run_optimize(req, calibration)`), `jobs.py` (`JobStore` single-slot lock over a spawn-context `ProcessPoolExecutor`, one-shot supervisor coroutine), `worker.py` (child-process entrypoint fed the parent's calibration snapshot)
- REST endpoints: synchronous `POST /backtest`; asynchronous `POST /optimizer/jobs` (`202` + `job_id`, `409` when busy), `GET /optimizer/jobs/{id}`, `GET /optimizer/jobs`
- Pydantic request/response schemas for the new endpoints in `api/schemas.py`
- Telegram notifications on optimizer start, completion, and failure
- `optuna` pinned in `requirements.txt`
- `plan/phase-10-trading-tools-integration.md` — execution plan for this phase

### Changed
- `trading/backtest.py` rewritten from a CLI script into a pure `run_backtest(req) -> BacktestResult` library entry point that builds an `EngineConfig` and reuses the calibration cache
- `trading/optimize_params.py` renamed to `trading/optimizer/search.py`; exhaustive parameter grid replaced by an Optuna TPE search
- `trading/market_analyzer.py` reduced to library-only; `analyze_structural_noise` no longer takes `print_results` / `show_events` / `volatility_level`
- Pure-Python engine met the wall-clock budget, so the optional Numba JIT (Appendix A of the phase plan) was evaluated and not adopted — no compiled toolchain added to the image
- `README.md`, `ROADMAP.md`, and `docs/operations.md` updated to document the new endpoints and mark Phase 10 complete

### Removed
- CLI entry points (`_parse_args`, `if __name__ == "__main__"`, `main`) from `backtest.py`, the optimizer, and `market_analyzer.py`
- Exhaustive-grid constants (`STOP_PCT_CHOICES`, `K_ACT_CHOICES`, `MIN_MARGIN_CHOICES`) and `_iter_exhaustive_candidates` from the optimizer
- `print_pair_argument_error`, `print_statistics`, `print_events_detail`, `print_structural_noise_results` from `core/utils.py` — orphaned after the CLI removal

---

## [2.9.0] – Phase 9: Project Documentation & Portfolio Framing

### Added
- `docs/configuration.md` — complete `.env` variable reference extracted from `README.md`
- `docs/trading-strategy.md` — ATR classification, K_STOP calibration, position lifecycle, and invariants extracted from `README.md` and inline comments
- `docs/operations.md` — local dev, production deploy, manual rollback, self-hosting, monitoring, and troubleshooting procedures
- `CHANGELOG.md` following [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format, starting from Phase 0
- `plan/phase-9-project-documentation.md` — execution plan for this phase

### Changed
- `README.md` revamped as an engineering cover letter: architecture diagram (Mermaid), key decisions table, PostgreSQL ERD, data-flow diagram, and quick start; all deep configuration and strategy content moved to `docs/`
- Key decisions table: phase numbers dropped in favour of technology names, `Plan` column renamed to `Reference`, all links routed through `ROADMAP.md` anchors instead of plan files directly
- `ROADMAP.md`: added execution plan links for phases 8 and 9; removed non-existent plan link from phase 10; corrected all `github.com/jAjiz/BoTC` URLs to `github.com/jAjiz/BoTCoin`
- `CLAUDE.md`: updated service count to four, added `SessionRecord` ORM model description, added design choices for phases 5–8
- `.github/workflows/ci.yml`: corrected `raw.githubusercontent.com` URLs to `BoTCoin` repo name

---

## [2.8.0] – Phase 8: Observability — Grafana Dashboard

### Added
- `sessions` table (Alembic migration `20260512_01`) capturing start/end timestamps, completion status, balance snapshot, per-pair market data, and log lines per scheduler tick
- `grafana_reader` Postgres role with read-only grants on all five application tables
- Grafana 11 service in `docker-compose.yml`, provisioned entirely from repository-managed YAML and JSON files under `services/grafana/`
- "BoTC Overview" pre-built dashboard with four rows: market metrics, performance metrics, system state, and session history
- `_SessionLogCollector` handler in `core/scheduler.py` that captures log records for `sessions.log_messages`

### Changed
- `trading_session()` opens a `sessions` row at the top and finalises it (with status + captured data) in a `finally` block regardless of session outcome

---

## [2.7.0] – Phase 7: CI/CD Pipeline

### Added
- `docker-compose.prod.yml` — production override replacing `build:` with `image: ghcr.io/jajiz/botc:${IMAGE_TAG:-main}` for `botc` and `telegram`
- `.github/workflows/ci.yml` — unified five-job pipeline: `Lint (ruff)`, `Unit tests`, `Integration tests`, `Build and push image`, `Deploy to VPS`; lint and tests gate the build; build gates the deploy

### Removed
- `.github/workflows/deploy.yml` — superseded by `ci.yml`

---

## [2.6.0] – Phase 6: Code Quality — Linting & Type Safety

### Added
- `pyproject.toml` as the single source of truth for `ruff`, `pytest`, and coverage configuration
- `ruff` pinned in `requirements-dev.txt`
- Full argument and return-type annotations on every public function across `core/`, `exchange/`, `trading/`, `services/telegram/`, `api/`, and `scripts/`
- `_safe_call` helper in `exchange/kraken.py` collapsing the repeated query/error-log/return-None pattern
- `_to_decimal_required` helper in `core/database.py` for non-nullable Decimal conversions

### Changed
- Logging convention normalised: `import logging as stdlib_logging` alongside `import core.logging as logging` wherever both are needed
- All `Optional[X]` annotations replaced with `X | None`; all `List[X]` with `list[X]`
- Inline `TODO` comments replaced with GitHub issue links

### Removed
- `pytest.ini` — configuration moved to `pyproject.toml`
- `.coveragerc` — configuration moved to `pyproject.toml`

---

## [2.5.0] – Phase 5: REST API Layer — FastAPI

### Added
- `api/` package: `GET /market`, `GET /positions`, `GET /balance`, `GET /status`, `POST /control/pause`, `POST /control/resume`
- `api/schemas.py` with Pydantic v2 response models
- `AsyncIOScheduler` started from the FastAPI `lifespan` hook with a dedicated `ThreadPoolExecutor`
- `services/telegram/` as an independent FastAPI service with PTB polling, `/notify` endpoint, and `httpx`-backed command handlers
- `API_SECRET_TOKEN` protecting all REST endpoints and the `/notify` webhook
- `botc` (`:8000`) and `telegram` (`:8001`) as two separate services in `docker-compose.yml`

### Changed
- `BlockingScheduler` replaced with `AsyncIOScheduler`
- Telegram command handlers refactored to delegate all reads and commands to the `botc` API via `httpx`

---

## [2.4.0] – Phase 4: Professional Persistence — PostgreSQL

### Added
- `core/database.py` — DAL with four ORM models (`OHLCData`, `ClosedPosition`, `TrailingState`, `BotControl`) and module-level functions
- Alembic migration `20260414_01_phase4_initial_schema.py` creating all four tables with appropriate indexes
- `scripts/load_legacy_data.py` — one-time migration script importing existing CSV/JSON data into PostgreSQL
- Full `postgres` service in `docker-compose.yml` with health check and named volume

### Removed
- Flat-file persistence (JSON for state, CSV for OHLC history) from the production path

---

## [2.3.0] – Phase 3: Testing Strategy

### Added
- `tests/unit/` — pure-logic test suite for `core/`, `trading/`, and `exchange/` (no network calls)
- `tests/integration/` — optional live-connectivity tests gated by `RUN_DB_INTEGRATION` and `RUN_KRAKEN_INTEGRATION`
- `docker-compose.test.yml` with a `test` service for running the suite inside Docker
- `pytest.ini` with markers (`unit`, `integration`) and an 80 % coverage gate

---

## [2.2.1] – Phase 2.1: API Efficiency

### Added
- Module-level rate limiter in `exchange/kraken.py` enforcing 1-second minimum between public API calls
- `TELEGRAM_ENABLED` flag in `.env` to disable Telegram initialisation without touching call sites

### Changed
- ATR calculation uses the penultimate candle (`iloc[-2]`) instead of the latest to avoid incomplete-candle bias

### Removed
- Blanket `time.sleep(1)` calls from the main loop and error handlers

---

## [2.2.0] – Phase 2: Managed Execution — APScheduler

### Added
- `apscheduler` as a runtime dependency
- `IntervalTrigger` job replacing the `while True` loop, with `max_instances=1`
- `SIGTERM`/`SIGINT` handlers calling `scheduler.shutdown(wait=True)` for graceful shutdown
- `call_with_retry` for read-only API calls (balance, prices, ATR)

### Removed
- Unmanaged `while True` loop from `main.py`

---

## [2.1.0] – Phase 1: Infrastructure — Docker

### Added
- `Dockerfile` using a Python 3.12 slim base image
- `docker-compose.yml` with `botc` and `postgres` service stubs
- `.dockerignore` excluding `.env`, `__pycache__`, `data/`, and test artefacts
- `.env.example` documenting all supported environment variables

---

## [2.0.0] – Phase 0: AI-Assisted Development Environment

### Added
- `.github/agents/`, `.github/instructions/`, `.github/skills/` infrastructure
- Awesome-Copilot agents, instructions, and skills for architectural consistency and accelerated delivery
- `CLAUDE.md` documenting project architecture, design choices, and collaboration conventions for AI-assisted development

---

## [1.0.0] – BoTCoin V1

Functional, modular trading bot with clean separation of concerns across `core/`, `exchange/`, `trading/`, and `services/`. Ran an unmanaged `while True` loop, persisted state and history as JSON and CSV flat files, and exposed a Telegram interface directly in-process. No linter, no CI test gate, no structured persistence layer. V2 was started to address these gaps; V1 changes are not retroactively documented.
