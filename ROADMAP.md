# BoTCoin V2 – Roadmap

This document outlines the improvement areas and phased plan for the next iteration of BoTCoin, with a focus on **Data Engineering and Cloud Architecture** principles. The goal is to evolve the project into a managed data pipeline with professional-grade persistence, observability, and testability.

---

## 📋 Table of Contents

- [Current State](#-current-state)
- [Improvement Areas](#-improvement-areas)
- [Phased Roadmap](#-phased-roadmap)
  - [Phase 0 - Setup AI-Assisted Development Environment (Completed)](#phase-0---setup-ai-assisted-development-environment-completed)
  - [Phase 1 – Infrastructure First: Docker (Completed)](#phase-1--infrastructure-first-docker-completed)
  - [Phase 2 – Managed Execution: APScheduler](#phase-2--managed-execution-apscheduler)
    - [Phase 2.1 – API Efficiency (Completed)](#phase-21--api-efficiency-completed)
  - [Phase 3 – Testing Strategy (Completed)](#phase-3--testing-strategy-completed)
  - [Phase 4 – Professional Persistence: PostgreSQL (Completed)](#phase-4--professional-persistence-postgresql)
  - [Phase 5 – REST API Layer: FastAPI](#phase-5--rest-api-layer-fastapi)
  - [Phase 6 – Code Quality: Linting & Type Safety](#phase-6--code-quality-linting--type-safety)
  - [Phase 7 – CI/CD Pipeline](#phase-7--cicd-pipeline)
  - [Phase 8 – Data Architecture Documentation](#phase-8--data-architecture-documentation)
  - [Phase 9 – Observability: Grafana Dashboard](#phase-9--observability-grafana-dashboard)
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
| Data architecture docs | ❌ No ERD or data model documentation |

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

### 8. Data Architecture Documentation
With the PostgreSQL-backed persistence layer in place, the data model must be documented explicitly. The PostgreSQL schema (ERD) should be added to `README.md` as the authoritative reference for understanding how the bot stores and accesses data.

### 9. Observability: Grafana Dashboard
With structured data in PostgreSQL, a Grafana service can expose market metrics, trading performance, and system health as persistent, queryable dashboards. Running Grafana as a Docker Compose service keeps the observability layer co-located with the rest of the stack and reproducible with a single `docker compose up`.

---

## 🚀 Phased Roadmap

Phases are ordered by dependency — each phase is a prerequisite for the next. Each phase is independently releasable.

---

### Phase 0 - Setup AI-Assisted Development Environment (Completed)

**Tracking:** [Issue #19](https://github.com/jAjiz/BoTCoin/issues/19), merged in [PR #20](https://github.com/jAjiz/BoTCoin/pull/20)

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

**Tracking:** [Issue #11](https://github.com/jAjiz/BoTCoin/issues/11), merged in [PR #22](https://github.com/jAjiz/BoTCoin/pull/22)

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

**Tracking:** [Issue #12](https://github.com/jAjiz/BoTCoin/issues/12), merged in [PR #24](https://github.com/jAjiz/BoTCoin/pull/24)

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

**Tracking:** [Issue #23](https://github.com/jAjiz/BoTCoin/issues/23), merged in [PR #24](https://github.com/jAjiz/BoTCoin/pull/24)

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

### Phase 5 – REST API Layer: FastAPI

**Goal:** Expose the bot's state and controls through a FastAPI service, and isolate Telegram as its own container so its long-lived polling lifecycle cannot stall the trading loop. The bot and API share a single process (splitting them would deliver no real benefit on a single-user bot and would force the in-memory runtime cache into Postgres solely to bridge containers).

**Scope:**

- [ ] Add `fastapi`, `uvicorn`, and `httpx` as runtime dependencies
- [ ] Swap `BlockingScheduler` for an `AsyncIOScheduler` started from a FastAPI `lifespan` hook, with a dedicated `ThreadPoolExecutor` so scheduler jobs never run on the API event loop
- [ ] Expose `GET /market`, `GET /positions`, `GET /balance`, `GET /status`, `POST /control/pause`, `POST /control/resume` (Swagger UI via FastAPI defaults)
- [ ] Add a global FastAPI exception handler so no route error can propagate into the scheduler thread
- [ ] Harden `core/runtime`: add `last_run_at`, drop the now-redundant `trailing_state` mirror, return copies from getters
- [ ] Split `services/telegram.py` into an independent FastAPI service that runs the polling loop, exposes `POST /notify`, and delegates all command handlers to the API via `httpx`
- [ ] Rewire `core/logging.py` so `to_telegram=True` posts to the Telegram service's `/notify` endpoint (best-effort, short timeout, errors swallowed)
- [ ] Update `docker-compose.yml`: run `botc` via `uvicorn`, add a `telegram` service, wire `API_BASE_URL` / `TELEGRAM_SERVICE_URL`

Detailed execution plan: [`plan/phase-5-fastapi.md`](plan/phase-5-fastapi.md).

**Success criteria:** The bot + API run in one container; Telegram runs in its own. Telegram consumes the API for every read and command, and receives notifications only via `/notify`. An unhandled route exception returns `500` and the scheduler keeps firing. A future UI can consume the API with no change to the bot or Telegram service.

---

### Phase 6 – Code Quality: Linting & Type Safety

**Goal:** Enforce consistent formatting and type safety across the entire codebase.

**Scope:**

- [ ] Add `ruff` to `requirements-dev.txt`
- [ ] Add a `pyproject.toml` configuring `ruff` (line length, enabled rule sets) and `pytest` (test paths, markers, coverage settings)
- [ ] Add type annotations to all public functions across:
  - `core/` modules
  - `exchange/kraken.py`
  - `trading/` modules
  - `services/telegram.py`
- [ ] Refactor repeated patterns into shared utilities (e.g., database client factory)
- [ ] Review and align exception handling: recoverable errors (log and retry/backoff in-session) vs. fatal errors (log and exit)

**Success criteria:** `ruff check .` and `ruff format --check .` pass cleanly. All public function signatures carry type annotations.

---

### Phase 7 – CI/CD Pipeline

**Goal:** Add lint and test quality gates that run inside Docker before any deployment step is allowed.

**Scope:**

- [ ] Add a `ci.yml` GitHub Actions workflow triggered on every pull request that:
  - Builds the Docker image
  - Runs `ruff check .` and `ruff format --check .` inside the container
  - Spins up the full `docker-compose.test.yml` stack and runs `pytest tests/unit` and `pytest tests/integration`
- [ ] Update the existing `deploy.yml` workflow to:
  - Depend on the `ci.yml` checks passing (via `workflow_run` trigger or branch protection rules)
  - Run tests inside the Docker container before executing the SSH deploy step
- [ ] Pin all GitHub Actions to specific commit SHAs (consistently — the `ssh-action` is already pinned; apply to `actions/checkout` and any new actions)
- [ ] Add CI status and Python version badges to `README.md`

**Success criteria:** A PR with a failing test or lint error is blocked from merging. The deploy workflow only runs after all checks pass on `main`.

---

### Phase 8 – Data Architecture Documentation

**Goal:** Document the V2 data architecture — the PostgreSQL schema and data flow — in `README.md` as the authoritative reference for understanding how the bot stores and accesses data.

**Scope:**

- [ ] Add a **Data Architecture** section to `README.md` covering:
  - **PostgreSQL ERD**: Entity-Relationship Diagram showing the `ohlc_data`, `closed_positions`, `trailing_state`, and `bot_control` tables, their columns, data types, primary keys, and indexes
  - **Data Flow Diagram**: illustrate how data moves from the Kraken API → PostgreSQL (OHLC, active state) → closed positions on trade close
- [ ] Add a `CHANGELOG.md` following [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format, tracking changes from the V2 milestone onwards (V1 history is not retroactively documented)
- [ ] Update the `README.md` Quick Start section to reflect the full V2 Docker Compose setup (bot + PostgreSQL)

**Success criteria:** A developer unfamiliar with the project can understand the full data model and how to query or inspect it using only the repository documentation.

---

### Phase 9 – Observability: Grafana Dashboard

**Goal:** Integrate Grafana as a persistent observability layer, connected directly to PostgreSQL, so that market, performance, and system metrics are always visible and the environment is fully reproducible.

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

## 🚫 Out of Scope

The following are intentionally excluded from the V2 roadmap:

- **Multi-exchange support** – Kraken-only scope is maintained for V2
- **Trading/management web UI** – Telegram interface remains the primary control surface; Grafana covers observability
- **Managed cloud databases** – PostgreSQL runs as a Docker Compose service; no RDS or equivalent managed services
- **Cloud infrastructure changes** – GCP free-tier VPS deployment model is retained; no Kubernetes or container orchestration platforms
- **Full async rewrite** – APScheduler covers periodic orchestration needs; a deeper async rewrite of all modules is deferred

---

*This roadmap will be updated as phases complete. Follow-up issues will be opened for each phase.*
