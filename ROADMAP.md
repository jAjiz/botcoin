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
    - [Phase 2.1 – API Performance & Data Quality (In Progress)](#phase-21--api-performance--data-quality-in-progress)
  - [Phase 3 – Testing Strategy](#phase-3--testing-strategy)
  - [Phase 4 – Professional Persistence: PostgreSQL & Redis](#phase-4--professional-persistence-postgresql--redis)
  - [Phase 5 – Code Quality: Linting & Type Safety](#phase-5--code-quality-linting--type-safety)
  - [Phase 6 – CI/CD Pipeline](#phase-6--cicd-pipeline)
  - [Phase 7 – Data Architecture Documentation](#phase-7--data-architecture-documentation)
  - [Phase 8 – Observability: Grafana Dashboard](#phase-8--observability-grafana-dashboard)
- [Out of Scope](#-out-of-scope)

---

## 🔍 Current State

BoTCoin V1 is a functional, modular trading bot with a clean separation of concerns across its packages (`core/`, `exchange/`, `trading/`, `services/`). The codebase is well-structured and documented via a comprehensive `README.md`.

Key gaps identified before starting V2 work:

| Area | Current Status |
|---|---|
| Infrastructure | ❌ No containerization — runs directly on bare VM |
| Execution model | ⚠️ Unmanaged `while True` loop — no retries, no observability |
| Testing | ❌ No unit or integration tests |
| Persistence | ⚠️ JSON + CSV flat files — no schema, no history guarantees |
| Active state store | ⚠️ JSON file on disk — not appropriate for real-time key-value access |
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
No automated tests exist. A two-tier test suite — unit tests for pure trading logic and integration tests for API connectivity and database persistence — provides the confidence needed to refactor safely and deploy reliably. All tests must run inside the Docker environment to ensure parity with production.

### 4. Professional Persistence: PostgreSQL & Redis
The flat-file persistence model (JSON for state, CSV for history) has no schema enforcement, no transactional guarantees, and no migration path. V2 adopts a two-tier database architecture aligned with the access patterns of each data category:
- **PostgreSQL** for structured, queryable historical data (OHLC candles, closed positions)
- **Redis** for real-time, low-latency key-value access (active trailing stop state, current orders)

Both services are defined in `docker-compose.yml`, requiring no external infrastructure.

### 5. Code Quality: Linting & Type Safety
Consistent formatting and type annotations improve IDE support, reduce cognitive overhead, and make the codebase more accessible for future contributors. `ruff` provides fast, zero-config linting and formatting as a single tool.

### 6. CI/CD Pipeline
The current pipeline deploys on every push to `main` with no validation. Tests must run inside Docker before any deployment step is allowed, ensuring what is tested is exactly what is deployed.

### 7. Data Architecture Documentation
With a two-tier persistence layer in place, the data model must be documented explicitly. The PostgreSQL schema (ERD) and the Redis key-value structure should be added to `README.md` as the authoritative reference for understanding how the bot stores and accesses data.

### 8. Observability: Grafana Dashboard
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
  - Includes `postgres` and `redis` service stubs (to be fully configured in Phase 4)
  - Supports running the bot (`main.py`) and the analysis scripts (`trading/market_analyzer.py`, `trading/backtest.py`)
- [x] Add a `.dockerignore` file to exclude `.env`, `__pycache__`, `data/`, and other non-essential files
- [x] Add a `.env.example` file documenting every supported environment variable
- [x] Update the `README.md` Quick Start section with Docker-based instructions

**Success criteria:** `docker compose up` starts the bot with a valid `.env` file, matching current manual setup behavior. No Python environment setup is required on the host machine.

---

### Phase 2 – Managed Execution: APScheduler

**Goal:** Replace the unmanaged `while True` loop in `main.py` with an APScheduler-driven periodic execution model, giving every session predictable scheduling, robust retry control, and graceful shutdown.

**Scope:**

- [ ] Add `apscheduler` as a runtime dependency
- [ ] Refactor `main.py` to replace the `while True` loop with an APScheduler entrypoint:
  - Create a single `IntervalTrigger` job for the trading session
  - Configure `max_instances=1` to prevent overlapping runs
  - Keep retry/backoff logic explicit around Kraken API calls
- [ ] Implement graceful shutdown:
  - Register signal handlers (`SIGTERM`, `SIGINT`) that call `scheduler.shutdown(wait=True)`
  - Allow the current running job to complete and persist state before exiting

**Success criteria:** The bot runs as a single APScheduler periodic job with no overlapping executions. Transient API failures are retried with backoff. `SIGTERM`/`SIGINT` triggers a clean shutdown that lets the current job finish.

---

#### Phase 2.1 – API Efficiency (Completed)

**Goal:** Improve API efficiency and data reliability by implementing rate limiting on public Kraken calls, ensuring OHLC data excludes incomplete candles, and streamlining the main bot loop.

**Scope:**

- [x] Implement a thread-safe, module-level rate limiter to ensure all public API calls respect the 1-second minimum interval.
- [x] Use the penultimate ATR value (`iloc[-2]`) instead of the latest value to ensure position calculations are based only on fully closed candles
- [x] Remove unnecessary blanket delays (`time.sleep(1)`) from the main loop and error handlers.

---

### Phase 3 – Testing Strategy

**Goal:** Implement a two-tier test suite (unit + integration) that runs entirely inside Docker, ensuring test parity with the production environment.

**Scope:**

- [ ] Add `pytest` and `pytest-cov` as development dependencies in `requirements-dev.txt`
- [ ] Create a `tests/` directory with the following structure:
  ```
  tests/
  ├── unit/
  │   ├── trading/        # ATR calculation, pivot detection, position logic
  │   ├── core/           # Validation, utils
  │   └── conftest.py
  └── integration/
      ├── test_kraken.py  # API connectivity (requires live credentials, opt-in)
      └── conftest.py
  ```
- [ ] **Unit tests** – cover pure-logic functions with no external dependencies:
  - `trading/market_analyzer.py`: ATR calculation, pivot detection, noise analysis
  - `trading/parameters_manager.py`: volatility level mapping, K parameter calculation
  - `trading/positions_manager.py`: position creation, trailing stop updates, close logic
  - `trading/inventory_manager.py`: portfolio valuation, balance logic
  - `core/validation.py`: all configuration edge cases
  - `core/utils.py`: utility functions
  - Use `unittest.mock` to stub all exchange API calls and database clients
- [ ] **Integration tests** – verify API connectivity:
  - Kraken API: authenticated balance fetch, OHLC retrieval (skipped if credentials absent)
- [ ] Add a `pytest.ini` or `pyproject.toml` section for test discovery, coverage thresholds, and markers (`unit`, `integration`)
- [ ] Add a `docker-compose.test.yml` override (or a dedicated `test` service) for running the full suite in CI

**Success criteria:** `docker compose run test pytest tests/unit` passes with no external network calls. `docker compose run test pytest tests/integration` passes with valid Kraken credentials.

---

### Phase 4 – Professional Persistence: PostgreSQL & Redis

**Goal:** Migrate all data storage from flat files to a two-tier database architecture. PostgreSQL handles structured historical data; Redis manages real-time active state.

**Scope:**

#### PostgreSQL (historical data & closed positions)
- [ ] Define the `ohlc_data` and `closed_positions` table schemas
- [ ] Write an Alembic migration (`scripts/migrations/`) to create both tables with appropriate indexes
- [ ] Update `trading/market_analyzer.py` to read and write OHLC data from/to PostgreSQL instead of CSV files
- [ ] Update `core/state.py`'s `save_closed_position` to write to the `closed_positions` table
- [ ] Write a one-time migration script (`scripts/migrate_to_postgres.py`) to import existing CSV and JSON data into PostgreSQL on upgrade

#### Redis (active trailing stop state)
- [ ] Define the Redis key-value schema (documented in Phase 7):
  - Active position: `botcoin:state:{pair}` → JSON-serialised position dict (mirrors current `trailing_state.json` structure per pair)
  - Bot control flag: `botcoin:control:paused` → `"1"` / `"0"` (replaces `telegram.BOT_PAUSED` in-memory flag)
- [ ] Update `core/state.py`'s `load_trailing_state` and `save_trailing_state` to read/write from Redis
- [ ] Update `services/telegram.py` to set and read the pause flag from Redis instead of an in-memory variable
- [ ] Add `data/` JSON and CSV files to `.gitignore`; document the new `data/` as containing only ephemeral migration inputs

#### docker-compose.yml
- [ ] Fully configure the `postgres` service with a named volume, health check, and init script for schema creation
- [ ] Fully configure the `redis` service with a named volume and `appendonly yes` for durability
- [ ] Add `DATABASE_URL` and `REDIS_URL` to `.env.example`

**Success criteria:** The bot runs with no flat files. OHLC data is queryable from PostgreSQL. Active positions survive a bot restart via Redis. Existing data is migrated cleanly.

---

### Phase 5 – Code Quality: Linting & Type Safety

**Goal:** Enforce consistent formatting and type safety across the entire codebase.

**Scope:**

- [ ] Add `ruff` to `requirements-dev.txt`
- [ ] Add a `pyproject.toml` configuring `ruff` (line length, enabled rule sets) and `pytest` (test paths, markers, coverage settings)
- [ ] Add type annotations to all public functions across:
  - `core/` modules
  - `exchange/kraken.py`
  - `trading/` modules
  - `services/telegram.py`
- [ ] Refactor repeated patterns into shared utilities (e.g., database client factory, Redis key builders)
- [ ] Review and align exception handling: recoverable errors (log and retry/backoff in-session) vs. fatal errors (log and exit)

**Success criteria:** `ruff check .` and `ruff format --check .` pass cleanly. All public function signatures carry type annotations.

---

### Phase 6 – CI/CD Pipeline

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

### Phase 7 – Data Architecture Documentation

**Goal:** Document the V2 data architecture — the PostgreSQL schema and the Redis key-value structure — in `README.md` as the authoritative reference for understanding how the bot stores and accesses data.

**Scope:**

- [ ] Add a **Data Architecture** section to `README.md` covering:
  - **PostgreSQL ERD**: Entity-Relationship Diagram showing the `ohlc_data` and `closed_positions` tables, their columns, data types, primary keys, and indexes
  - **Redis Key-Value Structure**: document every key pattern, its value format (type + JSON schema), TTL policy if any, and which component reads/writes it
  - **Data Flow Diagram**: illustrate how data moves from the Kraken API → PostgreSQL (OHLC) and Redis (active state) → closed positions (Redis → PostgreSQL on close)
- [ ] Add a `CHANGELOG.md` following [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format, tracking changes from the V2 milestone onwards (V1 history is not retroactively documented)
- [ ] Update the `README.md` Quick Start section to reflect the full V2 Docker Compose setup (bot + PostgreSQL + Redis)

**Success criteria:** A developer unfamiliar with the project can understand the full data model and how to query or inspect it using only the repository documentation.

---

### Phase 8 – Observability: Grafana Dashboard

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
- **Managed cloud databases** – PostgreSQL and Redis run as Docker Compose services; no RDS, ElastiCache, or equivalent managed services
- **Cloud infrastructure changes** – GCP free-tier VPS deployment model is retained; no Kubernetes or container orchestration platforms
- **Full async rewrite** – APScheduler covers periodic orchestration needs; a deeper async rewrite of all modules is deferred

---

*This roadmap will be updated as phases complete. Follow-up issues will be opened for each phase.*
