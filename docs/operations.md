# Operations Guide

---

## Local development

### Prerequisites

- Docker and Docker Compose v2 (no host Python required to run the bot)
- A copy of `.env` filled in from `.env.example` — see [configuration.md](configuration.md)

### Start the full stack

```bash
cp .env.example .env   # fill in required values
docker compose up -d --build
```

| Container | Port | Role |
|---|---|---|
| `botc` | `8000` | FastAPI trading engine + APScheduler |
| `botc-telegram` | `8001` | Telegram bot + `/notify` webhook |
| `botc-postgres` | `5432` | PostgreSQL (all state + history) |
| `botc-grafana` | `3000` | Grafana observability dashboard |

After startup:

- Swagger UI: `http://localhost:8000/docs`
- Grafana: `http://localhost:3000` (anonymous Viewer; use `admin` credentials for edits)

Watch logs: `docker compose logs -f botc`

Stop: `docker compose down`

### Running tests

```bash
# Unit tests (no external services required)
docker compose -f docker-compose.test.yml run --rm test pytest tests/unit

# Full suite (starts an ephemeral Postgres)
docker compose -f docker-compose.test.yml run --rm \
  -e POSTGRES_PASSWORD=botc \
  -e GRAFANA_DB_PASSWORD=local \
  -e RUN_DB_INTEGRATION=true \
  test pytest tests/

# Lint + format check
docker compose -f docker-compose.test.yml run --rm test ruff check .
docker compose -f docker-compose.test.yml run --rm test ruff format --check .

# Auto-fix
docker compose -f docker-compose.test.yml run --rm test ruff check . --fix
docker compose -f docker-compose.test.yml run --rm test ruff format .
```

The 80 % coverage gate is enforced by `pyproject.toml`.

---

## Production deployment (VPS)

### CI/CD automated deploy

Every push to `main` that passes lint and tests deploys automatically via `.github/workflows/ci.yml`. The pipeline builds a new image, pushes it to GHCR, and SSHes to the VPS to run `docker compose pull && up -d`.

### First deploy (manual setup)

```bash
# On the VPS — run once
mkdir -p ~/BoTC
# Place .env at ~/BoTC/.env (scp, paste from password manager, etc.)

COMMIT_SHA=$(git rev-parse HEAD)   # or the target commit SHA
curl -fsSL "https://raw.githubusercontent.com/jAjiz/BoTCoin/${COMMIT_SHA}/docker-compose.yml" \
  -o ~/BoTC/docker-compose.yml
curl -fsSL "https://raw.githubusercontent.com/jAjiz/BoTCoin/${COMMIT_SHA}/docker-compose.prod.yml" \
  -o ~/BoTC/docker-compose.prod.yml

cd ~/BoTC
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Verify: `curl http://localhost:8000/status`

### Manual rollback

Every `push: main` CI run tags the image twice: `:main` (moving) and `:sha-<short>` (immutable). To roll back without reverting the commit on `main`:

```bash
cd ~/BoTC
export IMAGE_TAG=sha-abc1234   # the last known-good SHA
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --remove-orphans
```

> **Database note**: rolling back the image does not rewind the database. If the broken release applied an Alembic migration, run `alembic downgrade -1` before rolling back the image.

To return to the latest `main`:

```bash
unset IMAGE_TAG
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --remove-orphans
```

---

## Monitoring

### Grafana

The "BoTC Overview" dashboard is available at `http://localhost:3000` (dev) or via SSH tunnel on the VPS (`ssh -L 3000:localhost:3000 <vps>`). It is provisioned automatically on every container start from `services/grafana/dashboards/botc.json`.

To edit the dashboard: make changes in the UI, use `Share → Export → Save to file` (with `Export for sharing externally` unchecked), and replace `services/grafana/dashboards/botc.json`. UI edits to the provisioned dashboard are blocked (`allowUiUpdates: false`); use "Save as" to create an experimental copy.

### Telegram commands

| Command | Description |
|---|---|
| `/help` | List commands and configured pairs |
| `/status` | Operational state (RUNNING / PAUSED) |
| `/pause` | Pause trading (current session completes before halt) |
| `/resume` | Resume trading |
| `/market [pair]` | Current price, ATR, volatility level, and balances |
| `/positions [pair]` | Open positions with P&L estimate |

### Health and status endpoints

| Endpoint | Response |
|---|---|
| `GET /health` | `200 OK` when the service is up |
| `GET /status` | JSON: `paused`, `last_run_at` |

### Trading tools — backtest & optimizer

The V1 CLI analysis scripts are now HTTP endpoints on the `botc` service: they run
in-process against stored OHLC (and the live calibration cache) and never mutate
trading state. All require the `X-Api-Token` header.

> A ready-to-run REST Client collection covering every endpoint — status, market,
> positions, balance, control, backtest, and optimizer — lives in
> [`api/requests.http.example`](../api/requests.http.example). Copy it to
> `api/requests.http` (gitignored) and set your token at the top of the file.

#### Backtest (synchronous)

`POST /backtest` simulates the strategy over stored OHLC and returns the result
inline (well under a second on 60 days of 15-min data).

```bash
curl -X POST http://localhost:8000/backtest \
  -H "X-Api-Token: $API_SECRET_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"pair":"XBTEUR","fee_pct":0.4}'
```

| Field | Default | Meaning |
|---|---|---|
| `pair` | — | Required; must be a configured pair (else `400`) |
| `fee_pct` | `0.0` | Per-side fee as a percentage (e.g. `0.4`, Kraken's current worst case) |
| `start` / `end` | `null` | Optional date slice; recomputes calibration from the slice |
| `max_ops` | `null` | Cap on simulated operations |
| `use_live_config` | `false` | Reuse the live bot's cached calibration instead of recomputing |

The response carries a `summary` (op count, win rate, total PnL in EUR and %, fees,
best/worst/avg/median per-trade PnL, `row_count`, and `source`: `cache` \|
`recompute` \| `slice`) plus the full `operations` list.

#### Optimizer (asynchronous)

The optimizer is CPU-bound, so it runs in a spawned child process and is polled.
`POST /optimizer/jobs` returns `202` with a `job_id`; a second submission while one
is running returns `409`. Results persist to the `optimizer_jobs` table and survive
restarts (a job interrupted by a restart is marked `failed`, never left `running`).
Telegram is notified on start, completion, and failure.

```bash
# Submit
JOB=$(curl -s -X POST http://localhost:8000/optimizer/jobs \
  -H "X-Api-Token: $API_SECRET_TOKEN" -H "Content-Type: application/json" \
  -d '{"pair":"XBTEUR","mode":"OPTIMIZE","fee_pct":0.4,"train_split":0.8,"n_trials":1000}' \
  | jq -r .job_id)

# Poll a single job
curl -s http://localhost:8000/optimizer/jobs/$JOB -H "X-Api-Token: $API_SECRET_TOKEN" | jq

# List recent jobs
curl -s "http://localhost:8000/optimizer/jobs?limit=20" -H "X-Api-Token: $API_SECRET_TOKEN" | jq
```

Each search runs two independent Optuna TPE studies — one over the `K_ACT`
activation branch, one over the `MIN_MARGIN` branch — and ranks the merged
candidates by robust PnL (the worse of the train/test halves). Calibration is on the
full OHLC history, exactly as the live bot does; the train/test split is evaluated in
a single continuous run (no mid-history reset).

| Mode | Behavior |
|---|---|
| `OPTIMIZE` | Run the TPE search at a fixed `n_trials` / `seed`; returns the ranked top candidates. |
| `CURRENT` | Evaluate the live `.env` config only (1 trial) — a baseline to compare against. |
| `AUTO` | Multi-seed convergence loop: run `OPTIMIZE` across `n_seeds` random seeds, escalating `n_trials` by `trial_step` until `min_agree` of them agree (or `max_trials` is hit), then compare the winner to `CURRENT` and report whether it improves on the live config. |

| Field | Default | Applies to | Meaning |
|---|---|---|---|
| `pair` | — | all | Required; must be a configured pair (else `400`) |
| `mode` | `OPTIMIZE` | all | `OPTIMIZE` \| `CURRENT` \| `AUTO` (else `422`) |
| `fee_pct` | `0.0` | all | Per-side fee percentage |
| `start` / `end` | `null` | all | Optional date slice |
| `train_split` | `0.8` | all | Train fraction for the train/test split (0.5–1.0) |
| `min_ops` / `min_test_ops` | `0` | OPTIMIZE, AUTO | Prune trials below these op counts |
| `n_trials` | `1000` | OPTIMIZE, AUTO | Optuna TPE trials (the initial count in AUTO) |
| `seed` | `42` | OPTIMIZE | Sampler seed |
| `n_seeds` | `4` | AUTO | Random seeds run per round (2–8) |
| `min_agree` | `3` | AUTO | Seeds that must converge to accept (2–8) |
| `trial_step` | `500` | AUTO | Trial increment per escalation (100–2000) |
| `max_trials` | `9000` | AUTO | Trial ceiling before giving up (500–20000) |

A completed job's `result` holds the ranked `top_candidates` (each with its
`k_act`/`min_margin`, per-level stop percentiles, and in-sample/train/test/robust
PnL) and ready-to-paste `suggested_env_lines`. AUTO results additionally report
`converged`, `is_improvement`, `current_robust_pnl`, `seeds_used`, `n_seeds_agreed`,
and `n_trials_at_convergence`. Applying them is manual: copy the suggested lines into
`.env` and redeploy (hot-reload of trading parameters is future work).

---

## Troubleshooting

### Bot never starts — database unreachable

`scripts/entrypoint.sh` runs `alembic upgrade head` before the app starts. If it fails, Postgres is likely not ready:

```bash
docker compose ps          # check postgres health status
docker compose logs postgres
```

Wait for the `pg_isready` health check to pass, then: `docker compose restart botc`.

### `GRAFANA_DB_PASSWORD` missing during Alembic migration

Migration `20260512_01` requires `GRAFANA_DB_PASSWORD` in the environment. Ensure it is set in `.env` before running `alembic upgrade head`.

### Session lag / missed ticks

Each session is CPU-bound during ATR calculation. If sessions consistently overrun `SLEEPING_INTERVAL`, inspect `SELECT id, EXTRACT(EPOCH FROM (ended_at - started_at)) AS duration_s FROM sessions ORDER BY id DESC LIMIT 20` to identify the slow sessions. Increasing `SLEEPING_INTERVAL` or trimming old `ohlc_data` rows reduces load.

### Database maintenance — trimming `ohlc_data`

OHLC rows accumulate indefinitely. To keep only the last 120 days per pair:

```sql
DELETE FROM ohlc_data
WHERE time < EXTRACT(EPOCH FROM NOW() - INTERVAL '120 days');
```

### Database maintenance — trimming `sessions`

Session rows also accumulate. No automated retention policy exists yet (noted in ROADMAP as future work). To trim manually:

```sql
DELETE FROM sessions WHERE started_at < NOW() - INTERVAL '90 days';
```

---

## Self-hosting on your own VPS

To run your own instance of BoTCoin using the built-in CI/CD pipeline:

1. **Fork the repository** on GitHub.
2. **Add the following secrets** in your fork under Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `VM_IP` | Public IP or hostname of your VPS |
| `VM_USER` | SSH user that has Docker access |
| `VM_KEY` | SSH private key for that user (paste the full PEM content) |
| `VM_DEPLOY_PATH` | Absolute path on the VPS for the deploy directory, e.g. `/home/<user>/BoTC` |

3. **Create `.env`** at `$VM_DEPLOY_PATH/.env` on the VPS with your Kraken, Telegram, Postgres, and Grafana credentials (copy from `.env.example` in the repo).
4. **Push to `main`** — the CI/CD pipeline builds the image, runs tests, and deploys automatically. The first push after the VPS is set up completes the initial deploy.

> The VPS needs Docker + Docker Compose v2 installed and the deploy user must be in the `docker` group (`sudo usermod -aG docker $USER`).
