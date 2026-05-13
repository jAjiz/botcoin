# Phase 8 – Observability: Grafana Dashboard

## Context

- Branch: `feature/phase-8-grafana` (already created)
- Prior phases delivered: Docker (Phase 1), APScheduler (Phase 2), pytest two-tier suite (Phase 3), PostgreSQL via SQLAlchemy + Alembic (Phase 4), FastAPI + Telegram split (Phase 5), `ruff` lint + format + type annotations (Phase 6), unified `ci.yml` with GHCR image-based deploy (Phase 7).
- Relevant files to read before starting:
  - `ROADMAP.md` — Phase 8 scope (authoritative)
  - `docker-compose.yml` — base compose; the `grafana` service is appended here so the local dev stack matches production
  - `docker-compose.prod.yml` — production override; no Grafana-specific changes needed (image not built locally)
  - `core/database.py` — `OHLCData`, `ClosedPosition`, `TrailingState`, `BotControl` ORM models — Grafana queries hit these four tables exclusively
  - `core/scheduler.py` — `trading_session()` runs once per `SLEEPING_INTERVAL`; gains a single heartbeat write at the end of the function
  - `scripts/migrations/versions/20260414_01_phase4_initial_schema.py` — reference style for Alembic migrations; Phase 8 adds one new revision
  - `.env.example` — every supported env var is documented here; Grafana adds three keys
  - `README.md` — Quick Start, Infrastructure sections (will receive a Grafana subsection and a dashboard screenshot stub)
- Architectural decisions:
  - **One Grafana container in the same Compose project**, on the same `botc_backend` network as Postgres. No external Grafana Cloud, no exposed-to-internet port — bind only to `127.0.0.1` like the other services.
  - **Read-only Postgres role for Grafana** (`grafana_reader`, password from env). Created via an Alembic migration so the existing migration pipeline owns the lifecycle. Grafana never connects as the bot's read/write user.
  - **Filesystem provisioning, not the HTTP API.** The datasource and dashboard JSON live under `grafana/` in the repo and are mounted into the container at `/etc/grafana/provisioning/`. Grafana applies them on every container start — no admin clicks, no manual export step on the running VPS.
  - **Dashboard JSON is hand-authored from a documented spec, then committed.** A developer builds the dashboard once in the Grafana UI (using the panel spec in Step 5), exports the JSON via the "Share → Export" dialog with `For external use` enabled, and commits the file. Subsequent edits go through the same export round-trip — no in-place UI edits on the deployed instance.
  - **No new application data is collected.** The four existing tables plus a single `last_heartbeat` row in `bot_control` cover every panel. Application logs and a metrics endpoint stay out of scope; if a panel cannot be expressed as SQL over these tables, it is not in this phase.
  - **Anonymous read-only access for the local instance**, single-admin login for any non-local deploy. The Grafana service binds to `127.0.0.1:3000` only, so anonymous read access is acceptable for a single-user setup; the admin login still gates write access.

## Target architecture

```
┌──────────────────────────────────────┐
│  botc (uvicorn :8000)                │
│  └─ scheduler                        │
│       └─ trading_session()           │
│            └─ bot_control.set(       │
│                  'last_heartbeat',   │
│                  now_utc())          │
└────────────────┬─────────────────────┘
                 │ SQLAlchemy (read/write as POSTGRES_USER)
                 ▼
┌──────────────────────────────────────┐                ┌──────────────────────────┐
│  postgres :5432                      │ ◄────────────  │  grafana :3000           │
│   ohlc_data                          │  read-only     │   provisioned datasource │
│   closed_positions                   │  as            │   provisioned dashboard  │
│   trailing_state                     │  grafana_reader│   (volume: gf_data)      │
│   bot_control                        │                │                          │
└──────────────────────────────────────┘                └────────────┬─────────────┘
                                                                     │
                                                                     ▼
                                                          127.0.0.1:3000 (browser)
```

Three application services + Postgres + Grafana. Grafana connects to Postgres over the shared Docker network using a least-privilege role; the bot is the only writer to every table in the database.

---

## Step 0 — Repository layout for Grafana assets

Create the directory tree that the Grafana container will mount. The exact paths matter — Grafana looks for files at hard-coded provisioning locations.

```
grafana/
├── provisioning/
│   ├── datasources/
│   │   └── postgres.yaml
│   └── dashboards/
│       └── botc.yaml          # provider config, not the dashboard JSON
└── dashboards/
    └── botc.json              # the dashboard payload
```

The split between `grafana/provisioning/dashboards/botc.yaml` (the *provider config* — tells Grafana where to find dashboards) and `grafana/dashboards/botc.json` (the *dashboard payload*) is required by Grafana — they are two different files with two different schemas.

Add `grafana/.gitkeep`-style placeholders only if a directory would otherwise be empty after Step 1. The directories above all receive real content in Steps 3–5; no `.gitkeep` needed.

No commit yet — this step is just the mental layout.

---

## Step 1 — Heartbeat write in the scheduler

The Phase 8 system-metrics panels need a single Postgres-observable signal that proves the scheduler is alive. The cleanest source is `bot_control` — the table already exists with a generic key/value shape and matching DAL (`set_control_value`), and Phase 5 already uses it for the `bot_paused` flag.

### 1.1 Append the heartbeat write to `trading_session()`

Edit `core/scheduler.py`. The current end of `trading_session()` is:

```python
    _session_count += 1
    runtime.update_last_run_at(now_utc())
    logging.info("======== SESSION COMPLETE ========")
```

Replace with:

```python
    _session_count += 1
    now = now_utc()
    runtime.update_last_run_at(now)
    db.set_control_value("last_heartbeat", now.isoformat(), updated_by="scheduler")
    logging.info("======== SESSION COMPLETE ========")
```

Implementation notes:
- `now_utc()` returns a timezone-aware `datetime`. `isoformat()` produces an RFC-3339 string Postgres can cast to `timestamptz` with `(control_value::timestamptz)`.
- Write **once per successful session**, after all per-pair work — a session that returns early (paused, balance fetch failed, prices fetch failed) intentionally does not advance the heartbeat. The panel will go red if the bot is stalled before the per-pair loop, which is the failure mode the panel is for.
- `updated_by="scheduler"` distinguishes this from `bot_paused` writes that come from the Telegram service (`updated_by="telegram"`).

### 1.2 Unit test for the heartbeat write

Add `tests/unit/core/test_scheduler.py` (create if missing) with one test:

```python
def test_trading_session_writes_heartbeat(monkeypatch):
    from datetime import datetime, UTC
    import core.scheduler as scheduler
    import core.database as db
    import core.runtime as runtime

    fixed_now = datetime(2026, 5, 12, 10, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(scheduler, "now_utc", lambda: fixed_now)
    monkeypatch.setattr(db, "get_bot_paused", lambda: False)
    monkeypatch.setattr(scheduler, "get_balance", lambda: {"EUR": "100"})
    monkeypatch.setattr(scheduler, "get_last_prices", lambda _pairs: {})
    monkeypatch.setattr(runtime, "update_balance", lambda _b: None)
    monkeypatch.setattr(runtime, "update_last_run_at", lambda _ts: None)

    calls: list[tuple] = []
    monkeypatch.setattr(
        db,
        "set_control_value",
        lambda key, value, updated_by=None: calls.append((key, value, updated_by)),
    )

    # PAIRS is empty when last_prices returns {}, so the inner loop is skipped.
    monkeypatch.setattr(scheduler, "PAIRS", [])

    scheduler.trading_session()

    assert calls == [("last_heartbeat", fixed_now.isoformat(), "scheduler")]
```

Run inside Docker: `docker compose -f docker-compose.test.yml run --rm test pytest tests/unit/core/test_scheduler.py -v`. Must pass.

**Commit:** `feat(scheduler): write last_heartbeat to bot_control after each session`

---

## Step 2 — Read-only Postgres role via Alembic migration

Create a new migration that provisions `grafana_reader` with `CONNECT` + `USAGE` + `SELECT` on the four data tables (and nothing else). Password comes from `GRAFANA_DB_PASSWORD` at upgrade time so it never lands in the repo.

### 2.1 Generate the migration

```
docker compose -f docker-compose.test.yml run --rm test alembic revision -m "grafana_reader role"
```

This creates `scripts/migrations/versions/<timestamp>_grafana_reader_role.py`. Rename the auto-generated revision id stub to `20260512_02` for date-ordered readability (the existing migration is `20260414_01`). The exact body:

```python
"""Phase 8: read-only Grafana role.

Revision ID: 20260512_02
Revises: 20260414_01
Create Date: 2026-05-12 00:00:00
"""

from __future__ import annotations

import os

from alembic import op

revision = "20260512_02"
down_revision = "20260414_01"
branch_labels = None
depends_on = None

GRAFANA_TABLES = ("ohlc_data", "closed_positions", "trailing_state", "bot_control")


def _escape_literal(value: str) -> str:
    return value.replace("'", "''")


def upgrade() -> None:
    password = os.environ.get("GRAFANA_DB_PASSWORD")
    if not password:
        raise RuntimeError(
            "GRAFANA_DB_PASSWORD must be set in the environment for migration 20260512_02. "
            "Set it in .env (it is also consumed by the grafana service)."
        )
    password_sql = _escape_literal(password)
    database = op.get_bind().engine.url.database

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
                CREATE ROLE grafana_reader LOGIN PASSWORD '{password_sql}';
            ELSE
                ALTER ROLE grafana_reader WITH LOGIN PASSWORD '{password_sql}';
            END IF;
        END
        $$;
        """
    )

    op.execute(f'GRANT CONNECT ON DATABASE "{database}" TO grafana_reader;')
    op.execute("GRANT USAGE ON SCHEMA public TO grafana_reader;")
    for table in GRAFANA_TABLES:
        op.execute(f"GRANT SELECT ON TABLE public.{table} TO grafana_reader;")

    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLES FROM grafana_reader;"
    )


def downgrade() -> None:
    database = op.get_bind().engine.url.database
    for table in GRAFANA_TABLES:
        op.execute(f"REVOKE SELECT ON TABLE public.{table} FROM grafana_reader;")
    op.execute("REVOKE USAGE ON SCHEMA public FROM grafana_reader;")
    op.execute(f'REVOKE CONNECT ON DATABASE "{database}" FROM grafana_reader;')
    op.execute("DROP ROLE IF EXISTS grafana_reader;")
```

Implementation notes:
- The migration is **idempotent on re-run**: the `DO $$` block creates the role only if missing and resets the password otherwise. This matters because `alembic upgrade head` runs on every container start (see `scripts/entrypoint.sh`).
- The password is interpolated as a Postgres string literal with `''`-escaping. The env var is operator-controlled, not user input — SQL injection is not a meaningful threat model here — but the escape keeps Postgres happy for passwords containing apostrophes.
- `op.get_bind().engine.url.database` reads the live database name (`DBbotc` by default) so the `GRANT CONNECT` line is correct even if the operator renames it.
- Explicitly **no** `ALL TABLES IN SCHEMA public` grant — `SELECT` is enumerated per table so adding a future write-only table (e.g. an event-log) does not silently leak rows to Grafana.

### 2.2 Test the migration against a live Postgres

Integration tests already require `RUN_DB_INTEGRATION=true`. Add `tests/integration/test_grafana_role.py`:

```python
import os

import pytest
from sqlalchemy import URL, create_engine, text

pytestmark = pytest.mark.integration

if os.environ.get("RUN_DB_INTEGRATION") != "true":
    pytest.skip("RUN_DB_INTEGRATION not set", allow_module_level=True)


def _reader_engine():
    password = os.environ["GRAFANA_DB_PASSWORD"]
    url = URL.create(
        drivername="postgresql+psycopg",
        username="grafana_reader",
        password=password,
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        database=os.environ.get("POSTGRES_DB", "DBbotc"),
    )
    return create_engine(url)


def test_grafana_reader_can_select_each_table():
    engine = _reader_engine()
    with engine.connect() as conn:
        for table in ("ohlc_data", "closed_positions", "trailing_state", "bot_control"):
            conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))


def test_grafana_reader_cannot_insert():
    engine = _reader_engine()
    with engine.connect() as conn, pytest.raises(Exception):
        conn.execute(
            text(
                "INSERT INTO bot_control (control_key, control_value) "
                "VALUES ('test_insert', 'x')"
            )
        )
        conn.commit()
```

Run: `docker compose -f docker-compose.test.yml run --rm -e GRAFANA_DB_PASSWORD=test test pytest tests/integration/test_grafana_role.py -v`. The CI integration job will pick this up automatically through the existing test discovery glob.

### 2.3 `.env.example`

Append:

```
# ==============================
# Grafana
# ==============================
# Password for the read-only Postgres role used by Grafana panels.
# Consumed by both the Alembic migration (`grafana_reader` creation) and the
# grafana service's datasource provisioning.
GRAFANA_DB_PASSWORD=change_me_with_a_strong_password

# Admin login for the Grafana UI. First-boot only — Grafana stores the
# bcrypt'd password in its volume after that.
GF_SECURITY_ADMIN_USER=admin
GF_SECURITY_ADMIN_PASSWORD=change_me_with_a_strong_password
```

The `GF_*` variable names are the exact names Grafana reads at startup — do not rename them.

### 2.4 CI integration job

`.github/workflows/ci.yml`'s `integration` job currently sets `RUN_DB_INTEGRATION=true` but not `GRAFANA_DB_PASSWORD`. The migration step will fail without it. Edit the integration job's `Apply Alembic migrations` and `Run integration tests` steps to add `-e GRAFANA_DB_PASSWORD=ci_grafana_password`:

```yaml
      - name: Apply Alembic migrations
        run: |
          docker compose -f docker-compose.test.yml run --rm \
            -e POSTGRES_PASSWORD=botc \
            -e GRAFANA_DB_PASSWORD=ci_grafana_password \
            test alembic upgrade head

      - name: Run integration tests
        run: |
          docker compose -f docker-compose.test.yml run --rm \
            -e POSTGRES_PASSWORD=botc \
            -e GRAFANA_DB_PASSWORD=ci_grafana_password \
            -e RUN_DB_INTEGRATION=true \
            test pytest tests/integration
```

**Commit:** `feat(db): add grafana_reader role migration and integration test`

---

## Step 3 — `grafana` service in `docker-compose.yml`

Append a `grafana` service to the base `docker-compose.yml` after the `postgres` block, and a named volume to the `volumes:` section. Production inherits this unchanged — `docker-compose.prod.yml` overrides only `botc` and `telegram` images, so Grafana on the VPS uses the same official image.

### 3.1 Service definition

```yaml
  grafana:
    image: grafana/grafana:11.4.0
    container_name: botc-grafana
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      GF_SECURITY_ADMIN_USER: ${GF_SECURITY_ADMIN_USER:-admin}
      GF_SECURITY_ADMIN_PASSWORD: ${GF_SECURITY_ADMIN_PASSWORD:?GF_SECURITY_ADMIN_PASSWORD must be set}
      GF_AUTH_ANONYMOUS_ENABLED: "true"
      GF_AUTH_ANONYMOUS_ORG_ROLE: "Viewer"
      GF_USERS_ALLOW_SIGN_UP: "false"
      GF_INSTALL_PLUGINS: ""
      # Consumed by datasources/postgres.yaml below.
      GRAFANA_DB_PASSWORD: ${GRAFANA_DB_PASSWORD:?GRAFANA_DB_PASSWORD must be set}
      POSTGRES_DB: ${POSTGRES_DB:-DBbotc}
    ports:
      - "127.0.0.1:3000:3000"
    volumes:
      - gf_data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
      - ./grafana/dashboards:/var/lib/grafana/dashboards:ro
    networks:
      - botc_backend
```

And in the bottom `volumes:` block, add:

```yaml
volumes:
  pg_data:
  gf_data:
```

Implementation notes:
- The image is pinned to `grafana/grafana:11.4.0`. Bump as a separate concern.
- Anonymous access (`Viewer`) is enabled because the port is bound to `127.0.0.1` only — Grafana on a public port without auth would be a vulnerability, but loopback-only is acceptable for the single-user setup. Anyone with shell access on the host can already read the database directly.
- `GF_INSTALL_PLUGINS: ""` is set explicitly to **prevent** Grafana from auto-installing anything on startup. The Postgres datasource is built in.
- Both provisioning mounts are `:ro` — the container must not be able to mutate the repo-managed config files.
- `gf_data` is the *only* volume Grafana writes to. Wiping it resets dashboard edit history and the admin bcrypt hash but does not lose provisioned dashboards or the datasource (those are re-read from the read-only mounts on next boot).

### 3.2 Verify Compose validates

```
docker compose -f docker-compose.yml config
```

Must exit `0` and emit a rendered config that includes the `grafana` service and the `gf_data` volume.

**Commit:** `feat(compose): add grafana service with named volume and provisioning mounts`

---

## Step 4 — Datasource provisioning

Create `grafana/provisioning/datasources/postgres.yaml`. Grafana reads this on every container start and (re-)creates the matching datasource — there is no need to click "Add datasource" in the UI.

```yaml
apiVersion: 1

datasources:
  - name: PostgreSQL
    type: postgres
    access: proxy
    uid: botc-postgres
    url: postgres:5432
    user: grafana_reader
    jsonData:
      database: ${POSTGRES_DB}
      sslmode: disable
      postgresVersion: 1600
      timescaledb: false
    secureJsonData:
      password: ${GRAFANA_DB_PASSWORD}
    editable: false
```

Implementation notes:
- `${POSTGRES_DB}` and `${GRAFANA_DB_PASSWORD}` are environment-variable expansions performed by Grafana itself (it supports `${VAR}` interpolation in provisioning YAML since v7.1). Both vars are wired into the container in Step 3.1.
- `uid: botc-postgres` is the stable identifier the dashboard JSON references — do not change it without updating every `datasource.uid` in the dashboard payload.
- `editable: false` makes the datasource read-only in the UI so an admin cannot accidentally repoint it. Edits go through the YAML.
- `postgresVersion: 1600` matches the `postgres:16-alpine` image. If the Postgres image is upgraded, bump this to keep Grafana's query planner aware of available features.

No dashboard provisioning yet — that comes in Step 5.

**Commit:** `feat(grafana): provision PostgreSQL datasource via filesystem`

---

## Step 5 — Dashboard provisioning and JSON

### 5.1 Provider config

Create `grafana/provisioning/dashboards/botc.yaml`:

```yaml
apiVersion: 1

providers:
  - name: BoTC dashboards
    orgId: 1
    folder: ""
    type: file
    disableDeletion: true
    updateIntervalSeconds: 30
    allowUiUpdates: false
    options:
      path: /var/lib/grafana/dashboards
      foldersFromFilesStructure: false
```

`allowUiUpdates: false` is deliberate — the dashboard JSON is the source of truth. Editing it in the UI without exporting back to the repo would create drift. Operators who want to experiment can fork a copy through "Save as".

### 5.2 Dashboard build procedure

The dashboard JSON is too long to hand-write line-by-line. Build it once in the Grafana UI from the panel spec below, then export with `Share → Export → Save to file` and the `Export for sharing externally` checkbox **off** (the dashboard stays internal). Commit the resulting file as `grafana/dashboards/botc.json`.

Before exporting, set the dashboard properties:
- Title: `BoTC Overview`
- UID: `botc-overview` (fixed — used in any future cross-references)
- Tags: `botc`, `trading`
- Time range default: `Last 7 days`
- Refresh interval: `1m`
- Variables: one variable named `pair`, type `Query`, datasource `PostgreSQL`, query `SELECT DISTINCT pair FROM ohlc_data ORDER BY 1`, `Multi-value: true`, `Include All option: true`.

### 5.3 Panel spec

Three rows, twelve panels total. All panels target the `PostgreSQL` datasource (uid `botc-postgres`). Time-series panels use Grafana's built-in `$__timeFilter()` macro so the dashboard's time range controls them automatically.

#### Row 1 — Market metrics

| # | Title | Panel type | Query |
|---|---|---|---|
| 1 | Close price | Time series | `SELECT to_timestamp("time") AS time, pair AS metric, close FROM ohlc_data WHERE pair IN ($pair) AND $__timeFilter(to_timestamp("time")) ORDER BY 1` |
| 2 | ATR | Time series | `SELECT to_timestamp("time") AS time, pair AS metric, atr FROM ohlc_data WHERE pair IN ($pair) AND $__timeFilter(to_timestamp("time")) AND atr IS NOT NULL ORDER BY 1` |
| 3 | Latest close per pair | Stat (repeat by `$pair`) | `SELECT close FROM ohlc_data WHERE pair = '$pair' ORDER BY "time" DESC LIMIT 1` |

#### Row 2 — Performance metrics

| # | Title | Panel type | Query |
|---|---|---|---|
| 4 | Total closed positions | Stat | `SELECT COUNT(*) FROM closed_positions WHERE $__timeFilter(closed_at)` |
| 5 | Cumulative PnL % | Stat | `SELECT COALESCE(SUM(pnl_percent), 0) FROM closed_positions WHERE $__timeFilter(closed_at)` |
| 6 | Win/loss ratio | Stat | `SELECT CASE WHEN SUM(CASE WHEN pnl_percent <= 0 THEN 1 ELSE 0 END) = 0 THEN NULL ELSE SUM(CASE WHEN pnl_percent > 0 THEN 1 ELSE 0 END)::float / SUM(CASE WHEN pnl_percent <= 0 THEN 1 ELSE 0 END)::float END FROM closed_positions WHERE $__timeFilter(closed_at)` |
| 7 | PnL per close | Bar chart | `SELECT closed_at AS time, pair AS metric, pnl_percent FROM closed_positions WHERE $__timeFilter(closed_at) AND pair IN ($pair) ORDER BY closed_at` |
| 8 | Cumulative PnL over time | Time series | `SELECT closed_at AS time, SUM(pnl_percent) OVER (ORDER BY closed_at) AS cumulative_pnl FROM closed_positions WHERE $__timeFilter(closed_at) ORDER BY closed_at` |
| 9 | Open positions | Table | `SELECT pair, side, entry_price, activation_price, trailing_price, stop_price, updated_at FROM trailing_state WHERE closing_order_id IS NULL AND pair IN ($pair) ORDER BY pair` |

#### Row 3 — System metrics

| # | Title | Panel type | Query |
|---|---|---|---|
| 10 | Seconds since last heartbeat | Stat (thresholds: green ≤ 120, yellow ≤ 300, red > 300) | `SELECT EXTRACT(EPOCH FROM (now() - (control_value::timestamptz))) FROM bot_control WHERE control_key = 'last_heartbeat'` |
| 11 | Bot paused | Stat (value mapping: `1` → "PAUSED" red, `0` → "RUNNING" green) | `SELECT (control_value = 'true')::int FROM bot_control WHERE control_key = 'bot_paused'` |
| 12 | OHLC ingestion rate | Time series | `SELECT date_trunc('hour', updated_at) AS time, pair AS metric, COUNT(*) AS rows FROM ohlc_data WHERE $__timeFilter(updated_at) AND pair IN ($pair) GROUP BY 1, pair ORDER BY 1` |

Implementation notes for the panel author:
- Every time-series query orders by `time ASC` because Grafana's Postgres datasource requires it for proper graph rendering.
- Panel 11's value mapping is a Grafana UI feature, not part of the SQL. Configure `Field overrides → Value mappings`.
- Panel 7 uses `Bar chart`, not `Time series`, because `pnl_percent` is a discrete per-close value, not a continuous metric.
- Panel 9 ("Open positions") deliberately reads `trailing_state` rather than synthesising from `closed_positions` — the table directly reflects the live state and updates within one tick.

### 5.4 Verify the dashboard JSON file

After exporting from the UI:

```
test -f grafana/dashboards/botc.json
python -c "import json; json.load(open('grafana/dashboards/botc.json'))"
```

Both must succeed. The JSON must contain `"uid": "botc-overview"` and at least twelve panel objects.

**Commit:** `feat(grafana): provision BoTC Overview dashboard (market + performance + system)`

---

## Step 6 — README updates

### 6.1 Quick Start

In the Quick Start section, after the `docker compose up` line, add:

```markdown
After the stack is running:

- API:       <http://localhost:8000/docs>
- Grafana:   <http://localhost:3000>  (anonymous Viewer access; `admin` login for edits)
```

### 6.2 New "Observability" section

Add this section to the Infrastructure block, immediately after the database subsection:

```markdown
### Observability — Grafana

A pre-provisioned Grafana instance ships with the stack. It runs as a Docker Compose service on `127.0.0.1:3000` and reads from PostgreSQL through a least-privilege `grafana_reader` role (created by Alembic migration `20260512_02`).

**What is on the default dashboard (`BoTC Overview`):**

- Market: close price and ATR per pair, latest close
- Performance: total closed positions, cumulative PnL %, win/loss ratio, per-close PnL, cumulative PnL over time, open positions table
- System: seconds since last scheduler heartbeat (thresholded), paused/running state, OHLC ingestion rate

**Provisioning:**

- `grafana/provisioning/datasources/postgres.yaml` — datasource (read-only, uid `botc-postgres`)
- `grafana/provisioning/dashboards/botc.yaml` — dashboard provider config
- `grafana/dashboards/botc.json` — the dashboard payload (committed to the repo)

The dashboard is the source of truth in the repo. To edit it, open the UI, save as a new dashboard, then `Share → Export → Save to file` and replace `grafana/dashboards/botc.json`. UI updates to the provisioned dashboard are disabled (`allowUiUpdates: false`) so changes cannot drift silently.
```

### 6.3 Screenshot stub

Add a `docs/screenshots/` directory and place a `grafana-overview.png` capture of the running dashboard after the first deploy. Reference it from the README hero section if Phase 9's revamp lands earlier; otherwise reference it from the new Observability section:

```markdown
![BoTC Overview dashboard](docs/screenshots/grafana-overview.png)
```

If the screenshot is unavailable at PR time (no live data yet), leave the reference line out and add it in a follow-up commit once the first VPS deploy has produced real panels.

**Commit:** `docs(readme): document Grafana observability layer and provisioning workflow`

---

## Step 7 — Final verification

### 7.1 Local stack smoke test

```
docker compose -f docker-compose.test.yml run --rm test ruff check .
docker compose -f docker-compose.test.yml run --rm test ruff format --check .
docker compose -f docker-compose.test.yml run --rm test pytest tests/unit
docker compose -f docker-compose.test.yml run --rm \
  -e POSTGRES_PASSWORD=botc \
  -e GRAFANA_DB_PASSWORD=local_grafana_password \
  -e RUN_DB_INTEGRATION=true \
  test pytest tests/integration
```

All four must pass. Coverage stays ≥ 80%.

### 7.2 Compose smoke test

```
docker compose up -d --build
```

Wait 90 seconds, then:

```
docker compose ps
# botc + telegram + postgres + grafana all healthy

curl -s http://localhost:8000/status
# paused: false, last_run_at: <iso timestamp>

curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/api/health
# 200

# Inspect the provisioned datasource (anonymous access works for read).
curl -s http://localhost:3000/api/datasources/uid/botc-postgres | jq '.name, .type'
# "PostgreSQL"
# "postgres"

# Inspect the provisioned dashboard.
curl -s http://localhost:3000/api/dashboards/uid/botc-overview | jq '.dashboard.title'
# "BoTC Overview"
```

Open `http://localhost:3000` in a browser. The `BoTC Overview` dashboard should render. With one full session under the bot's belt, panel 10 ("Seconds since last heartbeat") must show a value < `SLEEPING_INTERVAL + 30`.

### 7.3 Persistence smoke test

```
docker compose restart grafana
# Wait 10s, then:
curl -s http://localhost:3000/api/dashboards/uid/botc-overview | jq '.dashboard.title'
# "BoTC Overview"  — provisioning re-applies on every start
```

```
docker compose down
docker compose up -d
curl -s http://localhost:3000/api/dashboards/uid/botc-overview | jq '.dashboard.title'
# "BoTC Overview"  — gf_data volume preserves admin state; provisioning re-applies the dashboard
```

```
docker compose down
```

---

## Execution order (commits)

Each bullet is one focused commit. Run `pytest tests/unit` inside Docker after each commit.

1. `feat(scheduler): write last_heartbeat to bot_control after each session`
2. `feat(db): add grafana_reader role migration and integration test`
3. `feat(compose): add grafana service with named volume and provisioning mounts`
4. `feat(grafana): provision PostgreSQL datasource via filesystem`
5. `feat(grafana): provision BoTC Overview dashboard (market + performance + system)`
6. `docs(readme): document Grafana observability layer and provisioning workflow`

The PR can be opened after commit 6. The dashboard screenshot may land as a follow-up commit once the first deploy produces real panel data.

---

## Acceptance checklist

Run all of these before opening the PR:

- [ ] `grep -rn "last_heartbeat" core/` returns at least one match in `core/scheduler.py`.
- [ ] `docker compose -f docker-compose.test.yml run --rm test pytest tests/unit/core/test_scheduler.py -v` passes.
- [ ] `scripts/migrations/versions/20260512_02_*.py` exists, declares `down_revision = "20260414_01"`, and the body matches the spec in Step 2.1.
- [ ] `docker compose -f docker-compose.test.yml run --rm -e POSTGRES_PASSWORD=botc -e GRAFANA_DB_PASSWORD=ci_grafana_password test alembic upgrade head` exits `0`.
- [ ] `docker compose -f docker-compose.test.yml run --rm -e POSTGRES_PASSWORD=botc -e GRAFANA_DB_PASSWORD=ci_grafana_password -e RUN_DB_INTEGRATION=true test pytest tests/integration/test_grafana_role.py -v` passes both tests.
- [ ] `docker compose -f docker-compose.yml config | grep -A1 "container_name: botc-grafana"` shows the grafana service.
- [ ] `docker compose -f docker-compose.yml -f docker-compose.prod.yml config` still parses cleanly (grafana inherits unchanged).
- [ ] `grafana/provisioning/datasources/postgres.yaml` references `uid: botc-postgres` and `user: grafana_reader`.
- [ ] `grafana/provisioning/dashboards/botc.yaml` has `allowUiUpdates: false`.
- [ ] `python -c "import json; d = json.load(open('grafana/dashboards/botc.json')); assert d['uid'] == 'botc-overview'; assert len(d['panels']) >= 12"` exits `0`.
- [ ] `.env.example` documents `GRAFANA_DB_PASSWORD`, `GF_SECURITY_ADMIN_USER`, `GF_SECURITY_ADMIN_PASSWORD`.
- [ ] `.github/workflows/ci.yml` integration job passes `GRAFANA_DB_PASSWORD=ci_grafana_password` to both the migration step and the test step.
- [ ] `docker compose up -d` followed by 90s wait then `curl http://localhost:3000/api/health` returns `200`.
- [ ] `curl http://localhost:3000/api/dashboards/uid/botc-overview | jq -r '.dashboard.title'` returns `BoTC Overview`.
- [ ] After one full scheduler session, panel 10's underlying query (`SELECT EXTRACT(EPOCH FROM (now() - (control_value::timestamptz))) FROM bot_control WHERE control_key = 'last_heartbeat'`) returns a value below `SLEEPING_INTERVAL + 30`.
- [ ] `docker compose restart grafana` followed by re-reading the dashboard returns the same `BoTC Overview` payload (proves provisioning re-applies).
- [ ] `README.md` has an Observability — Grafana section listing the three provisioning files and the screenshot reference (screenshot file may be a follow-up commit).

---

## Non-goals for this phase

Explicitly out of scope — do not add any of these:

- **Alerting** (Grafana alerts, contact points, notification policies). Telegram already covers operational notifications; duplicating them through Grafana is a later concern.
- **Loki, Prometheus, OpenTelemetry, or any new metrics/logs pipeline.** The four existing Postgres tables plus a single `last_heartbeat` row are the entire observability data surface for Phase 8.
- **A dedicated error-rate panel.** That would require structured event logging, which is a Phase 8.x or later effort.
- **Multi-dashboard organisation** (folders, multiple JSON files per pair, per-strategy dashboards). One overview dashboard is enough until there is a second consumer.
- **Public exposure of the Grafana port.** Bound to `127.0.0.1` only; HTTPS / reverse proxy / SSO are not in scope.
- **Grafana plugin installation.** `GF_INSTALL_PLUGINS` is explicitly empty.
- **A CHANGELOG entry** (Phase 9 introduces the changelog).
- **Migrating the heartbeat to a dedicated metrics table.** `bot_control` already exists with the right shape; introducing a second key/value table for this single signal would be premature.
- **Refactoring `core/scheduler.py`** beyond appending the heartbeat write at the end of `trading_session()`. The exception-handling audit happened in Phase 6.
- **A `down`-tested migration.** `downgrade()` is written for completeness but rolling back `20260512_02` on a live system would break Grafana — and that is a deliberate one-way door at this stage.
