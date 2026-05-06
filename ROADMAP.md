# BoTCoin V2 – Roadmap

This document outlines the improvement areas and phased plan for the next iteration of BoTCoin, with a focus on **modern backend engineering practices**. The goal is to evolve the project into a production-grade backend service with professional persistence, observability, and testability.

---

## 📋 Table of Contents

- [Current State](#-current-state)
- [Improvement Areas](#-improvement-areas)
- [Phased Roadmap](#-phased-roadmap)
  - [Phase 0 - Setup AI-Assisted Development Environment (Completed)](#phase-0---setup-ai-assisted-development-environment-completed)
  - [Phase 1 – Infrastructure First: Docker (Completed)](#phase-1--infrastructure-first-docker-completed)
  - [Phase 2 – Managed Execution: APScheduler (Completed)](#phase-2--managed-execution-apscheduler)
    - [Phase 2.1 – API Efficiency (Completed)](#phase-21--api-efficiency-completed)
  - [Phase 3 – Testing Strategy (Completed)](#phase-3--testing-strategy-completed)
  - [Phase 4 – Professional Persistence: PostgreSQL (Completed)](#phase-4--professional-persistence-postgresql)
  - [Phase 5 – REST API Layer: FastAPI (Completed)](#phase-5--rest-api-layer-fastapi-completed)
  - [Phase 6 – Code Quality: Linting & Type Safety (Completed)](#phase-6--code-quality-linting--type-safety-completed)
  - [Phase 7 – CI/CD Pipeline](#phase-7--cicd-pipeline)
  - [Phase 8 – Observability: Grafana Dashboard](#phase-8--observability-grafana-dashboard)
  - [Phase 9 – Project Documentation & Portfolio Framing](#phase-9--project-documentation--portfolio-framing)
  - [Phase 10 – Trading Tools Integration: Backtest + Optimizer](#phase-10--trading-tools-integration-backtest--optimizer)
- [Out of Scope](#-out-of-scope)

---

## 🔍 Current State

BoTCoin V1 is a functional, modular trading bot with a clean separation of concerns across its packages (`core/`, `exchange/`, `trading/`, `services/`). The codebase is well-structured and documented via a comprehensive `README.md`.

Key gaps identified before starting V2 work:

| Area | Current Status |
|---|---|
| Infrastructure | ✅ Fully containerized with Docker and Docker Compose |
| Execution model | ⚠️ Unmanaged `while True` loop — no retries, no observability |
| Testing | ✅ Two-tier pytest suite with Docker parity and 80% coverage gate |
| Persistence | ⚠️ JSON + CSV flat files — no schema, no history guarantees |
| Active state store | ⚠️ JSON file on disk — no schema or transactional guarantees |
| CI pipeline | ⚠️ Deploy-only — no lint or test step before production |
| Code quality tooling | ❌ No linter or formatter configured |
| Project documentation | ⚠️ README covers usage but lacks architecture diagram, ERD, and portfolio framing |
| Trading analysis tooling | ⚠️ `backtest.py` and `optimize_params.py` are CLI-only and mutate global config |

---

## 🗺️ Improvement Areas

### 1. Infrastructure First: Docker
All development, testing, and production execution must happen inside containers. This eliminates environment drift, makes every dependency explicit, and ensures the same image is tested in CI and deployed to the VM. Docker Compose also acts as the local service registry, co-locating the bot with its database and cache dependencies.

### 2. Managed Execution: APScheduler
The current `while True` loop in `main.py` is opaque: failed API calls are swallowed, retries are manual, and there is no execution history. Replacing it with APScheduler gives each run a clear lifecycle with deterministic scheduling, centralized logging, and robust process-level shutdown handling without introducing a full orchestration platform.

### 3. Testing Strategy
The project now has a two-tier pytest suite: deterministic unit tests for business logic and opt-in integration tests for live Kraken connectivity. The suite runs locally and in Docker through a dedicated `test` service, and `pytest.ini` enforces markers plus an 80% coverage threshold across `core`, `trading`, and `exchange`.

### 4. Professional Persistence: PostgreSQL
The flat-file persistence model (JSON for state, CSV for history) has no schema enforcement, no transactional guarantees, and no migration path. V2 migrates all data storage to **PostgreSQL**, which covers every access pattern in the project:
- **Historical data**: OHLC candles and closed positions as queryable, indexed tables
- **Active state**: trailing stop state and bot control flags as regular rows — accessed infrequently enough that a relational store is sufficient and simpler to operate

PostgreSQL is the single persistence service defined in `docker-compose.yml`, requiring no external infrastructure.

### 5. REST API Layer: FastAPI
The bot's internal state is currently accessed directly by the Telegram service via shared in-process objects. Introducing a FastAPI service as the single external interface decouples every consumer — Telegram, future UIs, and external integrations — from the bot's internals, making each service independently deployable and testable.

### 6. Code Quality: Linting & Type Safety
Consistent formatting and type annotations improve IDE support, reduce cognitive overhead, and make the codebase more accessible for future contributors. `ruff` provides fast, zero-config linting and formatting as a single tool.

### 7. CI/CD Pipeline
The current pipeline deploys on every push to `main` with no validation. Tests must run inside Docker before any deployment step is allowed, ensuring what is tested is exactly what is deployed.

### 8. Observability: Grafana Dashboard
With structured data in PostgreSQL, a Grafana service can expose market metrics, trading performance, and system health as persistent, queryable dashboards. Running Grafana as a Docker Compose service keeps the observability layer co-located with the rest of the stack and reproducible with a single `docker compose up`.

### 9. Project Documentation & Portfolio Framing
The README is the project's cover letter. It must lead with engineering decisions, an architecture diagram, CI badges, and Grafana screenshots so a reader can grasp the project's scope and maturity in under a minute. Deep configuration reference and trading-strategy theory move out of the README into dedicated documents under `docs/`, keeping the top-level reading experience focused on the engineering story. The PostgreSQL ERD and data-flow diagram (originally a standalone phase) are folded in as one section among many.

### 10. Trading Tools Integration: Backtest + Optimizer
The V1 analysis scripts (`trading/backtest.py`, `trading/optimize_params.py`) are CLI-only and mutate global trading config — a hazard the live bot is currently isolated from only because they are invoked out-of-process. Folding them into the API as JSON endpoints — sync `/backtest`, async `/optimizer/jobs` with Postgres persistence and a single-slot `multiprocessing` worker — turns one-off scripts into reusable services without risking the live bot's config state, while introducing Numba JIT and Optuna TPE search to keep both endpoints fast.

---

## 🚀 Phased Roadmap

Phases are ordered by dependency — each phase is a prerequisite for the next. Each phase is independently releasable.

---

### Phase 0 - Setup AI-Assisted Development Environment (Completed)

**Tracking:** [Issue #19](https://github.com/jAjiz/BoTCoin/issues/19)

**Goal:** Establish a specialized AI-assisted development environment by integrating Awesome-Copilot resources. This ensures architectural consistency, security, and accelerated delivery for all subsequent V2 phases.

**Scope:**

- [x] Create the `.github/` infrastructure for AI assets:
  - `.github/agents/`
  - `.github/instructions/`
  - `.github/skills/`
- [x] Install and configure Awesome-Copilot recommended agents, instructions, and skills

**Success criteria:** Copilot identifies and applies project-specific rules without manual prompting.

---

### Phase 1 – Infrastructure First: Docker (Completed)

**Tracking:** [Issue #11](https://github.com/jAjiz/BoTCoin/issues/11)

**Goal:** Establish a fully containerized development and production environment. All subsequent phases build on top of this foundation.

**Scope:**

- [x] Write a `Dockerfile` using a Python slim base image for the production runtime
- [x] Write a `docker-compose.yml` that:
  - Defines the `botc` application service (builds from `Dockerfile`)
  - Loads credentials from a local `.env` file (never baked into the image)
  - Includes a `postgres` service stub (to be fully configured in Phase 4)
  - Supports running the bot (`main.py`) and the analysis scripts (`trading/market_analyzer.py`, `trading/backtest.py`)
- [x] Add a `.dockerignore` file to exclude `.env`, `__pycache__`, `data/`, and other non-essential files
- [x] Add a `.env.example` file documenting every supported environment variable
- [x] Update the `README.md` Quick Start section with Docker-based instructions

**Success criteria:** `docker compose up` starts the bot with a valid `.env` file, matching current manual setup behavior. No Python environment setup is required on the host machine.

---

### Phase 2 – Managed Execution: APScheduler (Completed)

**Tracking:** [Issue #12](https://github.com/jAjiz/BoTCoin/issues/12)

**Goal:** Replace the unmanaged `while True` loop in `main.py` with an APScheduler-driven periodic execution model, giving every session predictable scheduling, robust retry control, and graceful shutdown.

**Scope:**

- [ ] Add `apscheduler` as a runtime dependency
- [ ] Refactor `main.py` to replace the `while True` loop with an APScheduler entrypoint:
  - Create a single `IntervalTrigger` job for the trading session
  - Configure `max_instances=1` to prevent overlapping runs
- [ ] Implement graceful shutdown:
  - Allow the current running job to complete and persist state before exiting
  - Register signal handlers (`SIGTERM`, `SIGINT`) that call `scheduler.shutdown(wait=True)`
- [ ] Implement retry logic around read-only API calls (balance, prices, and ATR)

**Success criteria:** The bot runs as a single APScheduler periodic job with no overlapping executions. Read-only API failures are retried automatically. `SIGTERM`/`SIGINT` triggers a clean shutdown that lets the current job finish.

---

#### Phase 2.1 – API Efficiency (Completed)

**Tracking:** [Issue #23](https://github.com/jAjiz/BoTCoin/issues/23)

**Goal:** Improve API efficiency and data reliability by implementing rate limiting on public Kraken calls, ensuring OHLC data excludes incomplete candles, and streamlining the main bot loop.

**Scope:**

- [x] Implement a thread-safe, module-level rate limiter to ensure all public API calls respect the 1-second minimum interval.
- [x] Use the penultimate ATR value (`iloc[-2]`) instead of the latest value to ensure position calculations are based only on fully closed candles
- [x] Remove unnecessary blanket delays (`time.sleep(1)`) from the main loop and error handlers.
- [x] Add a `TELEGRAM_ENABLED` flag (`.env`) to disable Telegram initialization and notifications without touching any call site

---

### Phase 3 – Testing Strategy (Completed)

**Tracking:** [Issue #13](https://github.com/jAjiz/BoTCoin/issues/13)

**Goal:** Implement a two-tier test suite (unit + integration) that runs entirely inside Docker, ensuring test parity with the production environment.

**Scope:**

- [x] Add `pytest` and `pytest-cov` as development dependencies in `requirements-dev.txt`
- [x] Create a `tests/` directory with the implemented structure:
  ```
  tests/
  ├── integration/
  ├── unit/
  │   ├── core/           
  │   ├── exchange/       
  │   └── trading/              
  ```
- [x] **Unit tests** – cover pure-logic functions with no external dependencies:
  - Covers the `core`, `trading`, and `exchange` modules that contain business logic
  - Omits `trading/backtest.py` and `trading/optimize_params.py` because they are analysis scripts without core business logic
  - Omits `core/runtime.py` and `core/state.py` because they are thin shared-state and persistence wrappers with no business logic
  - Uses pytest monkeypatch-based stubs for exchange API calls so unit tests make no network calls
- [x] **Integration tests** – verify API connectivity:
  - Kraken API: authenticated balance fetch, OHLC retrieval (skipped if credentials absent)
- [x] Add a `pytest.ini` section for test discovery, coverage thresholds, and markers (`unit`, `integration`)
- [x] Add a dedicated `docker-compose.test.yml` `test` service for running the suite in Docker

**Success criteria:** `docker compose -f docker-compose.test.yml run --rm test pytest tests/unit` passes with no external network calls. `docker compose -f docker-compose.test.yml run --rm test pytest tests/integration` passes with valid Kraken credentials. The full suite runs with an 80% coverage gate.

---

### Phase 4 – Professional Persistence: PostgreSQL (Completed)

**Tracking:** [Issue #14](https://github.com/jAjiz/BoTCoin/issues/14)

**Goal:** Migrate all data storage from flat files to PostgreSQL. Every data category — historical OHLC data, closed positions, active trailing stop state, and bot control flags — is stored in a single, consistently managed relational database.

**Scope:**

#### PostgreSQL schema
- [x] Define the `ohlc_data`, `closed_positions`, `trailing_state`, and `bot_control` table schemas
- [x] Write an Alembic migration (`scripts/migrations/`) to create all tables with appropriate indexes
- [x] Update `trading/market_analyzer.py` to read and write OHLC data from/to PostgreSQL instead of CSV files
- [x] Update state persistence to write closed positions, trailing stop state, and bot control flags to PostgreSQL via the centralized DAL in `core/database.py` (replaces `core/state.py`)
- [x] Update `services/telegram.py` to read and write the pause flag from the `bot_control` table instead of an in-memory variable
- [x] Write a legacy data migration script (`scripts/load_legacy_data.py`) to import existing CSV and JSON data into PostgreSQL on upgrade

#### docker-compose.yml
- [x] Fully configure the `postgres` service with a named volume, health check, and `pg_isready` probe

**Success criteria:** The bot runs with no flat files. OHLC data is queryable from PostgreSQL. Active trailing stop state and the bot pause flag are persisted in PostgreSQL and survive a bot restart. Existing data is migrated cleanly.

---

### Phase 5 – REST API Layer: FastAPI (Completed)

**Tracking:** [Issue #15](https://github.com/jAjiz/BoTCoin/issues/15)

**Goal:** Expose the bot's state and controls through a FastAPI service, and isolate Telegram as its own container so its long-lived polling lifecycle cannot stall the trading loop. The bot and API share a single process (splitting them would deliver no real benefit on a single-user bot and would force the in-memory runtime cache into Postgres solely to bridge containers).

**Scope:**

- [x] Add `fastapi`, `uvicorn`, and `httpx` as runtime dependencies
- [x] Swap `BlockingScheduler` for an `AsyncIOScheduler` started from a FastAPI `lifespan` hook, with a dedicated `ThreadPoolExecutor` so scheduler jobs never run on the API event loop
- [x] Expose `GET /market`, `GET /positions`, `GET /balance`, `GET /status`, `POST /control/pause`, `POST /control/resume` (Swagger UI via FastAPI defaults)
- [x] Add a global FastAPI exception handler so no route error can propagate into the scheduler thread
- [x] Harden `core/runtime`: add `last_run_at`, drop the now-redundant `trailing_state` mirror, return copies from getters
- [x] Split `services/telegram.py` into an independent FastAPI service that runs the polling loop, exposes `POST /notify`, and delegates all command handlers to the API via `httpx`
- [x] Rewire `core/logging.py` so `to_telegram=True` posts to the Telegram service's `/notify` endpoint (best-effort, short timeout, errors swallowed)
- [x] Update `docker-compose.yml`: run `botc` via `uvicorn`, add a `telegram` service, wire `API_BASE_URL` / `TELEGRAM_SERVICE_URL`

Detailed execution plan: [`plan/phase-5-fastapi.md`](plan/phase-5-fastapi.md).

**Success criteria:** The bot + API run in one container; Telegram runs in its own. Telegram consumes the API for every read and command, and receives notifications only via `/notify`. An unhandled route exception returns `500` and the scheduler keeps firing. A future UI can consume the API with no change to the bot or Telegram service.

---

### Phase 6 – Code Quality: Linting & Type Safety (Completed)

**Tracking:** [Issue #26](https://github.com/jAjiz/BoTCoin/issues/26)

**Goal:** Enforce consistent formatting, complete type coverage, and predictable error handling across the codebase. Phases 4 and 5 introduced type-annotated modules; this phase extends that standard to the pre-Phase-4 modules and locks it in with `ruff`.

**Scope:**

- [x] Pin `ruff` in `requirements-dev.txt`
- [x] Add a `pyproject.toml` as the single source of truth for `ruff`, `pytest`, and coverage config (replaces `pytest.ini` and `.coveragerc`)
- [x] Annotate the remaining public functions in `core/`, `exchange/kraken.py`, `trading/`, `services/telegram/`, and `scripts/load_legacy_data.py`
- [x] Normalize the `core.logging` vs stdlib `logging` import convention; remove the `logger = logging.logging.getLogger(...)` indirection
- [x] Collapse repeated boilerplate (`exchange/kraken.py` `try/except/log/return None` shape, `Decimal(str(...))` conversions in `core/database.py`)
- [x] Audit `except Exception` blocks — classify each as recoverable (swallow + log + return sentinel) or fatal (propagate) and align the body to the role
- [x] Resolve or issue-track the inline `TODO` markers in `core/scheduler.py` and `core/database.py`

Detailed execution plan: [`plan/phase-6-code-quality.md`](plan/phase-6-code-quality.md).

**Success criteria:** `ruff check .` and `ruff format --check .` pass cleanly inside Docker. Every public function in the targeted modules carries argument and return-type annotations. The full test suite still passes the 80% coverage gate.

---

### Phase 7 – CI/CD Pipeline

**Tracking:** [Issue #31](https://github.com/jAjiz/BoTC/issues/31)

**Goal:** Replace the broken SSH-based deploy with a unified pipeline that gates quality on every PR, builds and publishes a container image on every push to `main`, and deploys that image to the VPS.

**Scope:**

- [ ] Add `docker-compose.prod.yml` — a Compose override that replaces `build:` with `image: ghcr.io/jajiz/botc:${IMAGE_TAG:-main}` for the `botc` and `telegram` services
- [ ] Add `.github/workflows/ci.yml` — a single unified workflow replacing `deploy.yml` with five jobs in `needs:` order:
  - `Lint (ruff)`, `Unit tests`, `Integration tests` — run on every PR and push
  - `Build and push image` — builds the production image and pushes `ghcr.io/jajiz/botc:main` + `:sha-<short>` to GHCR (push to `main` only)
  - `Deploy to VPS` — SSHes to the VPS, fast-forwards the deploy clone, runs `docker compose pull && up -d` (push to `main` only)
- [ ] Delete `.github/workflows/deploy.yml` — fully superseded by `ci.yml`
- [ ] Pin all GitHub Actions to specific commit SHAs; add CI status and Python version badges to `README.md`

Detailed execution plan: [`plan/phase-7-cicd.md`](plan/phase-7-cicd.md).

**Success criteria:** A PR with a failing lint or test is blocked. On every push to `main`, a fresh production image is published to GHCR and deployed to the VPS via the `ci.yml` job graph. The VPS keeps running the previous image if any gate fails.

---

### Phase 8 – Observability: Grafana Dashboard

**Goal:** Integrate Grafana as a persistent observability layer, connected directly to PostgreSQL, so that market, performance, and system metrics are always visible and the environment is fully reproducible. Lands before the documentation revamp so dashboard screenshots can be embedded directly in the new README.

**Scope:**

- [ ] Add a `grafana` service to `docker-compose.yml`:
  - Use the official `grafana/grafana` image
  - Configure a named volume for dashboard and datasource persistence so state survives container restarts
  - Expose the Grafana UI on a local port (e.g., `3000`)
- [ ] Provision a native PostgreSQL datasource automatically on startup (using Grafana's datasource provisioning directory)
- [ ] Create a comprehensive dashboard covering:
  - **Market metrics**: OHLC price history and ATR per pair
  - **Performance metrics**: closed position PnL over time, win/loss ratio, cumulative return
  - **System metrics**: scheduler run history (from application logs/postgres events), bot uptime, error rate
- [ ] Persist the dashboard JSON definition in the repository (`grafana/dashboards/`) so it is provisioned automatically on `docker compose up`
- [ ] Document the Grafana setup in `README.md` (port, default credentials, how to access)

**Success criteria:** `docker compose up` starts the bot, databases, and Grafana. The dashboard loads automatically with no manual configuration. Dashboard state persists across container restarts.

---

### Phase 9 – Project Documentation & Portfolio Framing

**Goal:** Restructure the project's documentation so the README reads as the engineering cover letter — architecture, decisions, badges, and screenshots — while moving deep configuration and trading-strategy theory into dedicated documents under `docs/`. This is the last phase before the project is published as a portfolio piece.

**Scope:**

#### `README.md` revamp (top-level reading experience)
- [ ] **Hero section**: tagline, one-paragraph problem statement, badges (CI status, coverage, Python version), one Grafana dashboard screenshot
- [ ] **Architecture diagram** (Mermaid, rendered inline by GitHub) showing the `botc` + `telegram` + `postgres` + `grafana` services, their responsibilities, and the data flow between them
- [ ] **Quick start** — `docker compose up` walkthrough, link to `.env.example`, link to Swagger UI
- [ ] **Key engineering decisions** — short bullets, each linking to the relevant phase plan under `plan/` (the planning artifacts themselves become part of the portfolio signal)
- [ ] **Data model section** — PostgreSQL ERD (`ohlc_data`, `closed_positions`, `trailing_state`, `bot_control`) and a data-flow diagram showing Kraken → Postgres → closed positions on trade close (folded in from the original Phase 8 scope)
- [ ] **Roadmap & future work** — link to `ROADMAP.md`; explicitly point to `plan/phase-10-trading-tools-integration.md` as a designed-but-unimplemented extension

#### `docs/` subfolder (deep references, not for first-time readers)
- [ ] `docs/configuration.md` — every `.env` variable, default, and effect (extracted from current README)
- [ ] `docs/trading-strategy.md` — ATR-based volatility regimes, K_STOP ladder, activation/trailing semantics (extracted from current README and inline code comments)
- [ ] `docs/operations.md` — running locally, deploying to the VPS, manual rollback, troubleshooting

#### Project metadata
- [ ] Add a `CHANGELOG.md` following [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format, tracking changes from the V2 milestone onwards (V1 history is not retroactively documented)
- [ ] Frame the project consistently as a **backend engineering project that happens to use crypto market data** — never lead with trading-bot positioning

**Success criteria:** A recruiter scanning the repo for under a minute can identify the project's scope, the engineering decisions made, and the maturity level (CI, observability, docs, planned extensions). A developer wanting to run, configure, or extend the project has a clear, single-source-of-truth document for each concern.

---

### Phase 10 – Trading Tools Integration: Backtest + Optimizer

**Goal:** Fold the V1 analysis scripts (`trading/backtest.py`, `trading/optimize_params.py`) into the FastAPI service as JSON endpoints, eliminating their global-state mutation hazard and making them reusable from any client. Introduce Numba JIT for the simulator core, an auto-lookback window selector via K_STOP stability sweep, an Optuna TPE search to replace the exhaustive grid, and a `multiprocessing.spawn` worker with a single-slot lock and Postgres-persisted job state for the long-running optimizer.

**Scope:**

- [ ] Add `trading/engine.py` — pure simulator with config-as-argument (`PairCalibration`, `EngineConfig`, `simulate_operations`); JIT-compile the inner loop with Numba
- [ ] Refactor `parameters_manager.calculate_trading_parameters` to auto-select the lookback window via a K_STOP stability sweep across `[30d, 45d, 60d, 90d, 120d, 180d, 240d, 365d]`; cache events + window in `core/runtime`
- [ ] Refactor `trading/market_analyzer.py` to library-only (drop CLI, drop `print_results`); delete the now-orphaned `print_*` helpers from `core/utils.py`
- [ ] Replace `trading/backtest.py`'s CLI with `run_backtest(req) -> BacktestResult`; sync endpoint `POST /backtest`
- [ ] Rename `trading/optimize_params.py` → `trading/optimizer.py`; replace exhaustive grid with Optuna TPE; expose `run_optimize(req) -> OptimizerResult`
- [ ] New `optimizer_jobs` Postgres table + Alembic migration; orphan-cleanup hook on FastAPI lifespan startup
- [ ] New `optimizer/` package: `JobStore` (in-memory single-slot lock + DB persistence), `worker.py` (multiprocessing.spawn entrypoint), supervisor task scheduled from FastAPI lifespan
- [ ] Endpoints: `POST /optimizer/jobs` (202 + `job_id`, 409 if busy), `GET /optimizer/jobs/{id}`, `GET /optimizer/jobs`
- [ ] Telegram notifications on optimizer start, completion, and failure
- [ ] Pin `numba` and `optuna` exactly in `requirements.txt`

Detailed execution plan: [`plan/phase-10-trading-tools-integration.md`](plan/phase-10-trading-tools-integration.md).

**Success criteria:** `POST /backtest` returns a populated result in under a second on 60d of 15-min OHLC. `POST /optimizer/jobs` returns a `job_id` immediately; results persist to Postgres; a second submission while one is running returns `409`. A crash mid-run leaves the row marked `failed` after the next startup, never `running` indefinitely. The two scripts in `trading/` no longer have CLI entry points and never mutate global trading config.

---

## 🚫 Out of Scope

The following are intentionally excluded from the V2 roadmap:

- **Multi-exchange support** – Kraken-only scope is maintained for V2
- **Trading/management web UI** – Telegram interface remains the primary control surface; Grafana covers observability
- **Managed cloud databases** – PostgreSQL runs as a Docker Compose service; no RDS or equivalent managed services
- **Cloud infrastructure changes** – GCP free-tier VPS deployment model is retained; no Kubernetes or container orchestration platforms
- **Full async rewrite** – APScheduler covers periodic orchestration needs; a deeper async rewrite of all modules is deferred

---

*This roadmap will be updated as phases complete. Follow-up issues will be opened for each phase.*
