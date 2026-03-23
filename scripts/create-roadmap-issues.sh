#!/usr/bin/env bash
# Creates one GitHub issue per BoTCoin V2 roadmap phase and assigns each to
# the "BoTCoin V2" milestone.
#
# Prerequisites:
#   - GitHub CLI (gh) installed and authenticated  (gh auth login)
#   - The "BoTCoin V2" milestone must already exist in the repository
#
# Usage:
#   chmod +x scripts/create-roadmap-issues.sh
#   ./scripts/create-roadmap-issues.sh

set -euo pipefail

REPO="jAjiz/BoTCoin"
MILESTONE="BoTCoin V2"

echo "Creating BoTCoin V2 roadmap issues in milestone \"${MILESTONE}\"..."

# ---------------------------------------------------------------------------
# Phase 1
# ---------------------------------------------------------------------------
gh issue create \
  --repo "${REPO}" \
  --milestone "${MILESTONE}" \
  --title "Phase 1 – Infrastructure First: Docker" \
  --body "## Goal

Establish a fully containerized development and production environment. All subsequent phases build on top of this foundation.

## Scope

- [ ] Write a \`Dockerfile\` using a Python slim base image for the production runtime
- [ ] Write a \`docker-compose.yml\` that:
  - Defines the \`botcoin\` application service (builds from \`Dockerfile\`)
  - Mounts the \`data/\` directory as a named volume for local persistence
  - Loads credentials from a local \`.env\` file (never baked into the image)
  - Includes \`postgres\` and \`redis\` service stubs (to be fully configured in Phase 4)
  - Supports running the bot (\`main.py\`) and the analysis scripts that have \`if __name__ == \"__main__\"\` entry points (\`trading/market_analyzer.py\`, \`trading/backtest.py\`)
- [ ] Add a \`.dockerignore\` file to exclude \`.env\`, \`__pycache__\`, \`data/\`, and other non-essential files
- [ ] Add a \`.env.example\` file documenting every supported environment variable with its type, default value, and a short description
- [ ] Update the \`README.md\` Quick Start section with Docker-based instructions

## Success criteria

\`docker compose up\` starts the bot with a valid \`.env\` file, matching current manual setup behavior. No Python environment setup is required on the host machine."

echo "✓ Phase 1 issue created"

# ---------------------------------------------------------------------------
# Phase 2
# ---------------------------------------------------------------------------
gh issue create \
  --repo "${REPO}" \
  --milestone "${MILESTONE}" \
  --title "Phase 2 – Managed Execution: Prefect Orchestration" \
  --body "## Goal

Replace the unmanaged \`while True\` loop in \`main.py\` with a Prefect-managed data pipeline, giving every session a structured lifecycle with native retries, observability, and graceful shutdown.

## Scope

- [ ] Add \`prefect\` as a runtime dependency
- [ ] Refactor \`main.py\` to use Prefect decorators:
  - Annotate the top-level trading session as a \`@flow\` (\`botcoin_session_flow\`)
  - Annotate each logical step as a \`@task\`
  - Configure retries on Kraken API tasks to replace manual error-and-sleep logic
- [ ] Route all task logs through Prefect's logging layer so execution history is visible in the Prefect UI
- [ ] Implement graceful shutdown:
  - Register signal handlers (\`SIGTERM\`, \`SIGINT\`) that set a shutdown flag
  - On shutdown, allow the current session to complete, persist state to the database (Phase 4), and close all database connections cleanly before exiting
- [ ] Update \`docker-compose.yml\` to include the Prefect server as an optional local service for UI-based run inspection

## Success criteria

The bot runs as a Prefect flow. Individual task failures trigger automatic retries. A clean shutdown persists state and closes connections. Run history is accessible via the Prefect UI."

echo "✓ Phase 2 issue created"

# ---------------------------------------------------------------------------
# Phase 3
# ---------------------------------------------------------------------------
gh issue create \
  --repo "${REPO}" \
  --milestone "${MILESTONE}" \
  --title "Phase 3 – Testing Strategy" \
  --body "## Goal

Implement a two-tier test suite (unit + integration) that runs entirely inside Docker, ensuring test parity with the production environment.

## Scope

- [ ] Add \`pytest\` and \`pytest-cov\` as development dependencies in \`requirements-dev.txt\`
- [ ] Create a \`tests/\` directory with the following structure:
  \`\`\`
  tests/
  ├── unit/
  │   ├── trading/        # ATR calculation, pivot detection, position logic
  │   ├── core/           # Validation, utils
  │   └── conftest.py
  └── integration/
      ├── test_kraken.py  # API connectivity (requires live credentials, opt-in)
      └── conftest.py
  \`\`\`
- [ ] **Unit tests** – cover pure-logic functions with no external dependencies:
  - \`trading/market_analyzer.py\`: ATR calculation, pivot detection, noise analysis
  - \`trading/parameters_manager.py\`: volatility level mapping, K parameter calculation
  - \`trading/positions_manager.py\`: position creation, trailing stop updates, close logic
  - \`trading/inventory_manager.py\`: portfolio valuation, balance logic
  - \`core/validation.py\`: all configuration edge cases
  - \`core/utils.py\`: utility functions
  - Use \`unittest.mock\` to stub all exchange API calls and database clients
- [ ] **Integration tests** – verify API connectivity:
  - Kraken API: authenticated balance fetch, OHLC retrieval (skipped if credentials absent)
- [ ] Add a \`pytest.ini\` or \`pyproject.toml\` section for test discovery, coverage thresholds, and markers (\`unit\`, \`integration\`)
- [ ] Add a \`docker-compose.test.yml\` override (or a dedicated \`test\` service) for running the full suite in CI

## Success criteria

\`docker compose run test pytest tests/unit\` passes with no external network calls. \`docker compose run test pytest tests/integration\` passes with valid Kraken credentials."

echo "✓ Phase 3 issue created"

# ---------------------------------------------------------------------------
# Phase 4
# ---------------------------------------------------------------------------
gh issue create \
  --repo "${REPO}" \
  --milestone "${MILESTONE}" \
  --title "Phase 4 – Professional Persistence: PostgreSQL & Redis" \
  --body "## Goal

Migrate all data storage from flat files to a two-tier database architecture. PostgreSQL handles structured historical data; Redis manages real-time active state.

## Scope

### PostgreSQL (historical data & closed positions)
- [ ] Define the \`ohlc_data\` and \`closed_positions\` table schemas
- [ ] Write an Alembic migration (\`scripts/migrations/\`) to create both tables with appropriate indexes
- [ ] Update \`trading/market_analyzer.py\` to read and write OHLC data from/to PostgreSQL instead of CSV files
- [ ] Update \`core/state.py\`'s \`save_closed_position\` to write to the \`closed_positions\` table
- [ ] Write a one-time migration script (\`scripts/migrate_to_postgres.py\`) to import existing CSV and JSON data into PostgreSQL on upgrade

### Redis (active trailing stop state)
- [ ] Define the Redis key-value schema (documented in Phase 7):
  - Active position: \`botcoin:state:{pair}\` → JSON-serialised position dict (mirrors current \`trailing_state.json\` structure per pair)
  - Bot control flag: \`botcoin:control:paused\` → \`\"1\"\` / \`\"0\"\` (replaces \`telegram.BOT_PAUSED\` in-memory flag)
- [ ] Update \`core/state.py\`'s \`load_trailing_state\` and \`save_trailing_state\` to read/write from Redis
- [ ] Update \`services/telegram.py\` to set and read the pause flag from Redis instead of an in-memory variable
- [ ] Add \`data/\` JSON and CSV files to \`.gitignore\`; document the new \`data/\` as containing only ephemeral migration inputs

### docker-compose.yml
- [ ] Fully configure the \`postgres\` service with a named volume, health check, and init script for schema creation
- [ ] Fully configure the \`redis\` service with a named volume and \`appendonly yes\` for durability
- [ ] Add \`DATABASE_URL\` and \`REDIS_URL\` to \`.env.example\`

## Success criteria

The bot runs with no flat files. OHLC data is queryable from PostgreSQL. Active positions survive a bot restart via Redis. Existing data is migrated cleanly."

echo "✓ Phase 4 issue created"

# ---------------------------------------------------------------------------
# Phase 5
# ---------------------------------------------------------------------------
gh issue create \
  --repo "${REPO}" \
  --milestone "${MILESTONE}" \
  --title "Phase 5 – Code Quality: Linting & Type Safety" \
  --body "## Goal

Enforce consistent formatting and type safety across the entire codebase.

## Scope

- [ ] Add \`ruff\` to \`requirements-dev.txt\`
- [ ] Add a \`pyproject.toml\` configuring \`ruff\` (line length, enabled rule sets) and \`pytest\` (test paths, markers, coverage settings)
- [ ] Add type annotations to all public functions across:
  - \`core/\` modules
  - \`exchange/kraken.py\`
  - \`trading/\` modules
  - \`services/telegram.py\`
- [ ] Refactor repeated patterns into shared utilities (e.g., database client factory, Redis key builders)
- [ ] Review and align exception handling: recoverable errors (log and retry via Prefect) vs. fatal errors (log and exit)

## Success criteria

\`ruff check .\` and \`ruff format --check .\` pass cleanly. All public function signatures carry type annotations."

echo "✓ Phase 5 issue created"

# ---------------------------------------------------------------------------
# Phase 6
# ---------------------------------------------------------------------------
gh issue create \
  --repo "${REPO}" \
  --milestone "${MILESTONE}" \
  --title "Phase 6 – CI/CD Pipeline" \
  --body "## Goal

Add lint and test quality gates that run inside Docker before any deployment step is allowed.

## Scope

- [ ] Add a \`ci.yml\` GitHub Actions workflow triggered on every pull request that:
  - Builds the Docker image
  - Runs \`ruff check .\` and \`ruff format --check .\` inside the container
  - Spins up the full \`docker-compose.test.yml\` stack and runs \`pytest tests/unit\` and \`pytest tests/integration\`
- [ ] Update the existing \`deploy.yml\` workflow to:
  - Depend on the \`ci.yml\` checks passing (via \`workflow_run\` trigger or branch protection rules)
  - Run tests inside the Docker container before executing the SSH deploy step
- [ ] Pin all GitHub Actions to specific commit SHAs (consistently — the \`ssh-action\` is already pinned; apply to \`actions/checkout\` and any new actions)
- [ ] Add CI status and Python version badges to \`README.md\`

## Success criteria

A PR with a failing test or lint error is blocked from merging. The deploy workflow only runs after all checks pass on \`main\`."

echo "✓ Phase 6 issue created"

# ---------------------------------------------------------------------------
# Phase 7
# ---------------------------------------------------------------------------
gh issue create \
  --repo "${REPO}" \
  --milestone "${MILESTONE}" \
  --title "Phase 7 – Data Architecture Documentation" \
  --body "## Goal

Document the V2 data architecture — the PostgreSQL schema and the Redis key-value structure — in \`README.md\` as the authoritative reference for understanding how the bot stores and accesses data.

## Scope

- [ ] Add a **Data Architecture** section to \`README.md\` covering:
  - **PostgreSQL ERD**: Entity-Relationship Diagram showing the \`ohlc_data\` and \`closed_positions\` tables, their columns, data types, primary keys, and indexes
  - **Redis Key-Value Structure**: document every key pattern, its value format (type + JSON schema), TTL policy if any, and which component reads/writes it
  - **Data Flow Diagram**: illustrate how data moves from the Kraken API → PostgreSQL (OHLC) and Redis (active state) → closed positions (Redis → PostgreSQL on close)
- [ ] Add a \`CHANGELOG.md\` following [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format, tracking changes from the V2 milestone onwards (V1 history is not retroactively documented)
- [ ] Update the \`README.md\` Quick Start section to reflect the full V2 Docker Compose setup (bot + PostgreSQL + Redis)

## Success criteria

A developer unfamiliar with the project can understand the full data model and how to query or inspect it using only the repository documentation."

echo "✓ Phase 7 issue created"

# ---------------------------------------------------------------------------
# Phase 8
# ---------------------------------------------------------------------------
gh issue create \
  --repo "${REPO}" \
  --milestone "${MILESTONE}" \
  --title "Phase 8 – Observability: Grafana Dashboard" \
  --body "## Goal

Integrate Grafana as a persistent observability layer, connected directly to PostgreSQL, so that market, performance, and system metrics are always visible and the environment is fully reproducible.

## Scope

- [ ] Add a \`grafana\` service to \`docker-compose.yml\`:
  - Use the official \`grafana/grafana\` image
  - Configure a named volume for dashboard and datasource persistence so state survives container restarts
  - Expose the Grafana UI on a local port (e.g., \`3000\`)
- [ ] Provision a native PostgreSQL datasource automatically on startup (using Grafana's datasource provisioning directory)
- [ ] Create a comprehensive dashboard covering:
  - **Market metrics**: OHLC price history and ATR per pair
  - **Performance metrics**: closed position PnL over time, win/loss ratio, cumulative return
  - **System metrics**: session execution history (from Prefect), bot uptime, error rate
- [ ] Persist the dashboard JSON definition in the repository (\`grafana/dashboards/\`) so it is provisioned automatically on \`docker compose up\`
- [ ] Document the Grafana setup in \`README.md\` (port, default credentials, how to access)

## Success criteria

\`docker compose up\` starts the bot, databases, and Grafana. The dashboard loads automatically with no manual configuration. Dashboard state persists across container restarts."

echo "✓ Phase 8 issue created"

echo ""
echo "All 8 roadmap issues created successfully."
