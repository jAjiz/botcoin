# Phase 8 – Observability: Grafana Dashboard

## Context

- Branch: `feature/phase-8-grafana` (already created)
- Prior phases delivered: Docker (Phase 1), APScheduler (Phase 2), pytest two-tier suite (Phase 3), PostgreSQL via SQLAlchemy + Alembic (Phase 4), FastAPI + Telegram split (Phase 5), `ruff` lint + format + type annotations (Phase 6), unified `ci.yml` with GHCR image-based deploy (Phase 7).
- Relevant files to read before starting:
  - `ROADMAP.md` — Phase 8 scope (authoritative)
  - `docker-compose.yml` — base compose; the `grafana` service is appended here so the local dev stack matches production
  - `docker-compose.prod.yml` — production override; no Grafana-specific changes needed (image not built locally)
  - `core/database.py` — `OHLCData`, `ClosedPosition`, `TrailingState`, `BotControl` ORM models — Grafana queries hit these four tables exclusively
  - `core/scheduler.py` — `trading_session()` runs once per `SLEEPING_INTERVAL`; gains session-tracking writes (a `sessions` row opened at the top and finalized in a `finally` block with status, balance, per-pair data, and the captured log buffer)
  - `scripts/migrations/versions/20260414_01_phase4_initial_schema.py` — reference style for Alembic migrations; Phase 8 adds one new revision
  - `.env.example` — every supported env var is documented here; Grafana adds three keys
  - `README.md` — Quick Start, Infrastructure sections (will receive a Grafana subsection and a dashboard screenshot stub)
- Architectural decisions:
  - **One Grafana container in the same Compose project**, on the same `botc_backend` network as Postgres. No external Grafana Cloud, no exposed-to-internet port — bind only to `127.0.0.1` like the other services.
  - **Read-only Postgres role for Grafana** (`grafana_reader`, password from env). Created via an Alembic migration so the existing migration pipeline owns the lifecycle. Grafana never connects as the bot's read/write user.
  - **Filesystem provisioning, not the HTTP API.** The datasource and dashboard JSON live under `grafana/` in the repo and are mounted into the container at `/etc/grafana/provisioning/`. Grafana applies them on every container start — no admin clicks, no manual export step on the running VPS.
  - **Dashboard JSON is hand-authored from a documented spec, then committed.** A developer builds the dashboard once in the Grafana UI (using the panel spec in Step 5), exports the JSON via the "Share → Export" dialog with `For external use` enabled, and commits the file. Subsequent edits go through the same export round-trip — no in-place UI edits on the deployed instance.
  - **One new application table is introduced: `sessions`.** Each scheduler tick writes one row capturing start/end timestamps, completion status, the balance snapshot, per-pair market data, and the log lines emitted during the session. The four existing tables plus this one cover every panel. A separate logs/metrics pipeline (Loki, Prometheus, OpenTelemetry) stays out of scope; if a panel cannot be expressed as SQL over these five tables, it is not in this phase.
  - **Anonymous read-only access for the local instance**, single-admin login for any non-local deploy. The Grafana service binds to `127.0.0.1:3000` only, so anonymous read access is acceptable for a single-user setup; the admin login still gates write access.

## Target architecture

```
┌──────────────────────────────────────┐
│  botc (uvicorn :8000)                │
│  └─ scheduler                        │
│       └─ trading_session()           │
│            ├─ db.create_session()    │
│            │   (status='running')    │
│            └─ db.finalize_session()  │
│                (status, balance,     │
│                 pair_data, logs)     │
└────────────────┬─────────────────────┘
                 │ SQLAlchemy (read/write as POSTGRES_USER)
                 ▼
┌──────────────────────────────────────┐                ┌──────────────────────────┐
│  postgres :5432                      │ ◄────────────  │  grafana :3000           │
│   ohlc_data                          │  read-only     │   provisioned datasource │
│   closed_positions                   │  as            │   provisioned dashboard  │
│   trailing_state                     │  grafana_reader│   (volume: gf_data)      │
│   bot_control                        │                │                          │
│   sessions                           │                │                          │
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

## Step 1 — `sessions` table and per-session telemetry

Phase 8 needs richer per-session telemetry than a single `last_heartbeat` timestamp can provide. A new `sessions` table records every scheduler tick — start/end timestamps, completion status, the balance snapshot, per-pair market data, and the log lines emitted during the session — so Grafana can plot session throughput, failure rate, and the data the bot saw without the operator needing to grep container logs. The "seconds since last successful session" panel is then derived from `MAX(ended_at) WHERE status = 'ok'`, replacing the original `bot_control.last_heartbeat` design.

### 1.1 ORM model

Add to `core/database.py` after the `BotControl` model:

```python
from sqlalchemy.dialects.postgresql import JSONB


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    balance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    pair_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    log_messages: Mapped[list | None] = mapped_column(JSONB, nullable=True)
```

`status` values: `running` (in-flight), `ok` (completed the per-pair loop end-to-end), `paused` (skipped because `bot_paused`), `failed` (early return due to balance/prices fetch failure or unhandled exception).

The JSONB shapes:
- `balance` — the raw dict returned by `exchange.kraken.get_balance` (e.g. `{"EUR": "123.45", "XBT": "0.001"}`). `NULL` if the balance fetch failed.
- `pair_data` — `{pair: {"price": float, "atr": float, "volatility_level": str}}`. Only pairs whose price + ATR fetched successfully appear.
- `log_messages` — `[{"ts": iso8601, "level": "INFO"|"WARNING"|"ERROR", "message": str}, ...]` in chronological order.

### 1.2 Alembic migration

A single Phase 8 migration creates the `sessions` table and provisions the read-only `grafana_reader` role + grants. They land together because the role's grant list includes `sessions`, and rolling one back without the other would leave the database in an inconsistent state for Grafana.

Add `scripts/migrations/versions/20260512_01_phase8_observability.py`:

```python
"""Phase 8: sessions table + grafana_reader role.

Revision ID: 20260512_01
Revises: 20260414_01
Create Date: 2026-05-12 00:00:00
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260512_01"
down_revision = "20260414_01"
branch_labels = None
depends_on = None

GRAFANA_TABLES = ("ohlc_data", "closed_positions", "trailing_state", "bot_control", "sessions")


def _escape_literal(value: str) -> str:
    return value.replace("'", "''")


def upgrade() -> None:
    # 1. sessions table — written to by the scheduler each tick.
    op.create_table(
        "sessions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("balance", JSONB, nullable=True),
        sa.Column("pair_data", JSONB, nullable=True),
        sa.Column("log_messages", JSONB, nullable=True),
    )
    op.create_index("ix_sessions_started_at", "sessions", ["started_at"], unique=False)

    # 2. grafana_reader role — read-only login used by the Grafana datasource.
    password = os.environ.get("GRAFANA_DB_PASSWORD")
    if not password:
        raise RuntimeError(
            "GRAFANA_DB_PASSWORD must be set in the environment for migration 20260512_01. "
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
    op.drop_index("ix_sessions_started_at", table_name="sessions")
    op.drop_table("sessions")
```

Implementation notes:
- **Idempotent on re-run**: the `DO $$` block creates `grafana_reader` only if missing and resets its password otherwise. This matters because `alembic upgrade head` runs on every container start (see `scripts/entrypoint.sh`). The `sessions` table is not re-created — Alembic's revision tracking handles that.
- The password is interpolated as a Postgres string literal with `''`-escaping. The env var is operator-controlled, not user input — SQL injection is not a meaningful threat model here — but the escape keeps Postgres happy for passwords containing apostrophes.
- `op.get_bind().engine.url.database` reads the live database name (`DBbotc` by default) so the `GRANT CONNECT` line is correct even if the operator renames it.
- Explicitly **no** `ALL TABLES IN SCHEMA public` grant — `SELECT` is enumerated per table so adding a future write-only table (e.g. an event-log) does not silently leak rows to Grafana.
- The `GRAFANA_DB_PASSWORD` requirement applies even to developers who only want the `sessions` table — the env var is already required by the Compose file and `.env.example`, so this introduces no new operator burden.

### 1.3 DAL functions

Add to `core/database.py`:

```python
def create_session(started_at: datetime) -> int:
    with SessionLocal() as s, s.begin():
        row = Session(started_at=started_at, status="running")
        s.add(row)
        s.flush()
        return row.id


def finalize_session(
    session_id: int,
    ended_at: datetime,
    status: str,
    balance: dict | None,
    pair_data: dict | None,
    log_messages: list[dict],
) -> None:
    with SessionLocal() as s, s.begin():
        s.execute(
            sa.update(Session)
            .where(Session.id == session_id)
            .values(
                ended_at=ended_at,
                status=status,
                balance=balance,
                pair_data=pair_data,
                log_messages=log_messages,
            )
        )
```

`create_session` returns the id so `trading_session()` can hold it across the body and pass it to `finalize_session` in the `finally` block.

### 1.4 Session log collector

The simplest way to capture every log line emitted during a session — including those from `positions_manager` and any other module called from `trading_session()` — is a `logging.Handler` attached to the root logger for the duration of the session. This avoids threading a context object through the call graph and keeps `core/logging.py` unchanged.

Add near the top of `core/scheduler.py`:

```python
import logging as std_logging
from datetime import UTC, datetime


class _SessionLogCollector(std_logging.Handler):
    """Captures records into a list for persistence at session end."""

    def __init__(self) -> None:
        super().__init__(level=std_logging.INFO)
        self.records: list[dict] = []

    def emit(self, record: std_logging.LogRecord) -> None:
        self.records.append(
            {
                "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
                "level": record.levelname,
                "message": record.getMessage(),
            }
        )
```

### 1.5 Wire the session into `trading_session()`

Replace the body of `trading_session()` so it opens a row at the top, captures `balance` and `pair_data` as they are computed, and finalizes the row in a `finally` block regardless of which path the session takes.

```python
def trading_session() -> None:
    global _session_count

    collector = _SessionLogCollector()
    root = std_logging.getLogger()
    root.addHandler(collector)

    started_at = now_utc()
    session_id = db.create_session(started_at)
    status = "failed"  # overwritten on success / paused
    current_balance: dict | None = None
    pair_data: dict[str, dict] = {}

    try:
        if db.get_bot_paused():
            logging.info("Bot is paused. Skipping session.\n")
            status = "paused"
            return

        logging.info("======== STARTING SESSION ========")
        trailing_state = {}

        current_balance = call_with_retry(get_balance)
        if current_balance is None:
            logging.error("Could not fetch balance. Skipping session.\n")
            return
        runtime.update_balance(current_balance)

        last_prices = call_with_retry(get_last_prices, PAIRS)
        if last_prices is None:
            logging.error("Could not fetch prices. Skipping session.\n")
            return

        for pair in PAIRS:
            logging.info(f"--- Processing pair: [{pair}] ---")
            trailing_state[pair] = db.load_trailing_state(pair)
            current_price = last_prices.get(pair, None)
            current_atr = call_with_retry(get_current_atr, pair)

            if current_price is None or current_atr is None:
                logging.error("Could not fetch price or ATR. Skipping this pair.")
                continue

            if _session_count % PARAM_SESSIONS == 0:
                calculate_trading_parameters(pair)

            vol_level = get_volatility_level(pair, current_atr)
            logging.info(f"Market: {current_price:,.1f}€ | ATR: {current_atr:,.1f}€ ({vol_level})")
            runtime.update_pair_data(pair, price=current_price, atr=current_atr, volatility_level=vol_level)
            pair_data[pair] = {
                "price": current_price,
                "atr": current_atr,
                "volatility_level": vol_level,
            }

            if is_closing_complete(trailing_state.get(pair)):
                db.save_closed_position(pair, trailing_state[pair])
                db.delete_trailing_state(pair)
                del trailing_state[pair]
                logging.info(f"Trailing position removed for {pair}.")

            if not trailing_state.get(pair):
                create_position(pair, current_balance, last_prices, current_atr, trailing_state)

            if is_open(trailing_state.get(pair)):
                tick_position(pair, trailing_state[pair], current_balance, last_prices, current_atr, trailing_state)

            if trailing_state.get(pair):
                db.save_trailing_state(pair, trailing_state[pair])
            else:
                db.delete_trailing_state(pair)

        _session_count += 1
        runtime.update_last_run_at(now_utc())
        logging.info("======== SESSION COMPLETE ========")
        status = "ok"
    except Exception:
        logging.exception("Unhandled exception in trading_session")
        status = "failed"
        raise
    finally:
        root.removeHandler(collector)
        db.finalize_session(
            session_id=session_id,
            ended_at=now_utc(),
            status=status,
            balance=current_balance,
            pair_data=pair_data,
            log_messages=collector.records,
        )
```

Implementation notes:
- `_session_count` only increments on the success path, so failed sessions do not advance the `PARAM_SESSIONS` cadence.
- `balance` is recorded only when the fetch succeeded — a failed session stores `NULL`, which Grafana can render as a gap.
- `pair_data` accumulates per-pair entries lazily, so a partial session still records what was seen for the pairs that succeeded before the error.
- The `except`/`raise` preserves APScheduler's existing traceback logging; the `finally` block guarantees the row is finalized either way.
- The collector is attached to the root logger, not the `core.logging` module logger — `logging.basicConfig` in `core/logging.py` routes everything through the root logger, so a single attach captures records from every module called during the session.

### 1.6 Unit tests

Add `tests/unit/core/test_scheduler.py`:

```python
from datetime import UTC, datetime

import core.database as db
import core.runtime as runtime
import core.scheduler as scheduler


def _patch_finalize(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(db, "create_session", lambda _started: 1)
    monkeypatch.setattr(db, "finalize_session", lambda **kwargs: calls.append(kwargs))
    return calls


def test_trading_session_records_successful_session(monkeypatch):
    fixed_now = datetime(2026, 5, 12, 10, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(scheduler, "now_utc", lambda: fixed_now)
    monkeypatch.setattr(db, "get_bot_paused", lambda: False)
    monkeypatch.setattr(scheduler, "get_balance", lambda: {"EUR": "100"})
    monkeypatch.setattr(scheduler, "get_last_prices", lambda _pairs: {})
    monkeypatch.setattr(runtime, "update_balance", lambda _b: None)
    monkeypatch.setattr(runtime, "update_last_run_at", lambda _ts: None)
    monkeypatch.setattr(scheduler, "PAIRS", [])
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    final = calls[0]
    assert final["session_id"] == 1
    assert final["status"] == "ok"
    assert final["balance"] == {"EUR": "100"}
    assert final["pair_data"] == {}
    assert any("SESSION COMPLETE" in m["message"] for m in final["log_messages"])


def test_trading_session_records_paused_session(monkeypatch):
    monkeypatch.setattr(scheduler, "now_utc", lambda: datetime(2026, 5, 12, 10, 0, 0, tzinfo=UTC))
    monkeypatch.setattr(db, "get_bot_paused", lambda: True)
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    assert calls[0]["status"] == "paused"
    assert calls[0]["balance"] is None
    assert calls[0]["pair_data"] == {}


def test_trading_session_records_failed_balance_fetch(monkeypatch):
    monkeypatch.setattr(scheduler, "now_utc", lambda: datetime(2026, 5, 12, 10, 0, 0, tzinfo=UTC))
    monkeypatch.setattr(db, "get_bot_paused", lambda: False)
    monkeypatch.setattr(scheduler, "get_balance", lambda: None)
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    assert calls[0]["status"] == "failed"
    assert calls[0]["balance"] is None
    assert any("Could not fetch balance" in m["message"] for m in calls[0]["log_messages"])
```

Run inside Docker: `docker compose -f docker-compose.test.yml run --rm test pytest tests/unit/core/test_scheduler.py -v`. All three must pass.

**Commit:** `feat(observability): sessions table, per-session telemetry, and grafana_reader role`

---

## Step 2 — Validate the read-only role and wire up CI

The migration that creates `grafana_reader` lives in Step 1.2 (folded into the unified Phase 8 migration). This step covers the supporting work: an integration test that proves the role's permissions, the `.env.example` entries it depends on, and the CI changes needed to pass `GRAFANA_DB_PASSWORD` through Docker Compose.

### 2.1 Test the migration against a live Postgres

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
        for table in ("ohlc_data", "closed_positions", "trailing_state", "bot_control", "sessions"):
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

### 2.2 `.env.example`

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

### 2.3 CI integration job

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

**Commit:** `test(db): grafana_reader integration test and CI wiring`

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

Four rows, twelve panels total. All panels target the `PostgreSQL` datasource (uid `botc-postgres`). The `pair` variable is **single-select, no All option** — queries use `pair = '$pair'` throughout.

#### Row 1 — System metrics

| # | Title | Panel type | Query |
|---|---|---|---|
| 1 | Bot paused | Stat (value mapping: `1` → "PAUSED" red, `0` → "RUNNING" green) | `SELECT (control_value = 'true')::int FROM bot_control WHERE control_key = 'bot_paused'` |
| 2 | Last successful session | Stat (unit: dateTimeAsLocal) | `SELECT MAX(ended_at) AS last_session FROM sessions WHERE status = 'completed'` |

#### Row 2 — Market metrics

| # | Title | Panel type | Query |
|---|---|---|---|
| 3 | Price | Candlestick | `SELECT to_timestamp("time") AS time, open::float AS open, high::float AS high, low::float AS low, close::float AS close FROM ohlc_data WHERE pair = '$pair' AND $__unixEpochFilter("time") ORDER BY 1` |
| 4 | ATR | Time series | `SELECT to_timestamp("time") AS time, atr::float AS atr FROM ohlc_data WHERE pair = '$pair' AND $__unixEpochFilter("time") AND atr IS NOT NULL ORDER BY 1` |
| 5 | Open positions | Table | `SELECT pair, side, entry_price, activation_price, trailing_price, stop_price, updated_at FROM trailing_state WHERE closing_order_id IS NULL AND pair = '$pair' ORDER BY pair` |

#### Row 3 — Performance metrics (all-time, no time filter)

| # | Title | Panel type | Query |
|---|---|---|---|
| 6 | Total closed positions | Stat | `SELECT COUNT(*) AS total FROM closed_positions WHERE pair = '$pair'` |
| 7 | Win/loss ratio | Stat | `SELECT COALESCE(SUM(CASE WHEN pnl_percent > 0 THEN 1.0 ELSE 0.0 END) / NULLIF(SUM(CASE WHEN pnl_percent <= 0 THEN 1.0 ELSE 0.0 END), 0), 0) AS win_loss_ratio FROM closed_positions WHERE pair = '$pair'` |
| 8 | PnL per close | Bar chart | `SELECT closed_at AS time, pnl_percent FROM closed_positions WHERE pair = '$pair' ORDER BY closed_at` |
| 9 | Cumulative PnL over time | Time series | `SELECT closed_at AS time, SUM(pnl_percent) OVER (ORDER BY closed_at) AS cumulative_pnl FROM closed_positions WHERE pair = '$pair' ORDER BY closed_at` |

#### Row 4 — Sessions

| # | Title | Panel type | Query |
|---|---|---|---|
| 10 | Sessions by status | Bar chart | `SELECT status, COUNT(*) AS sessions FROM sessions WHERE $__timeFilter(started_at) GROUP BY status ORDER BY status` |
| 11 | Recent sessions | Table | `SELECT id, started_at, ended_at, status, EXTRACT(EPOCH FROM (ended_at - started_at)) AS duration_s, CASE WHEN log_messages::text ILIKE '%Stop%' THEN 'close' WHEN log_messages::text ILIKE '%New%' THEN 'open' WHEN log_messages::text ILIKE '%Update%' OR log_messages::text ILIKE '%Recalibrate%' OR log_messages::text ILIKE '%Re-anchor%' THEN 'update' ELSE '-' END AS activity FROM sessions ORDER BY started_at DESC LIMIT 50` |
| 12 | Last session log | Table | `SELECT (msg->>'ts')::timestamptz AS time, msg->>'level' AS level, msg->>'message' AS message FROM sessions, jsonb_array_elements(log_messages) AS msg WHERE id = (SELECT MAX(id) FROM sessions) ORDER BY time` |

Implementation notes for the panel author:
- `ohlc_data.time` is a BigInt unix epoch — use `$__unixEpochFilter("time")` (not `$__timeFilter`). The `to_timestamp()` wrapper is only in the `SELECT` to give Grafana a proper time axis.
- Panel 3 (Candlestick) uses `format: table`; Grafana auto-maps columns named `open`, `high`, `low`, `close`.
- Panel 7 (Win/loss) uses `COALESCE(..., 0)` with explicit `1.0` literals to avoid integer division and to return `0` instead of `NULL` when there are no losing trades. The previous `THEN NULL` approach rendered as `0` in the stat panel.
- Performance panels (6–9) have no time filter — they show all-time stats for the selected pair.
- Panel 10 (Sessions by status) is a bar chart grouped by `status` — shows total count per status in the selected time range, not a time series.
- Panel 11 (Recent sessions) derives `activity` from `log_messages::text ILIKE` pattern matching: `'%Stop%'` → close, `'%New%'` → open, `'%Update%'`/`'%Recalibrate%'`/`'%Re-anchor%'` → update.
- Panel 12 (Last session log) is a plain `table` panel — raw rows of `time`, `level`, `message`. Simpler than the `Logs` panel type.

### 5.4 Verify the dashboard JSON file

After exporting from the UI:

```
test -f grafana/dashboards/botc.json
python -c "import json; json.load(open('grafana/dashboards/botc.json'))"
```

Both must succeed. The JSON must contain `"uid": "botc-overview"` and at least sixteen panel objects.

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

A pre-provisioned Grafana instance ships with the stack. It runs as a Docker Compose service on `127.0.0.1:3000` and reads from PostgreSQL through a least-privilege `grafana_reader` role (created by Alembic migration `20260512_01`).

**What is on the default dashboard (`BoTC Overview`):**

- Market: close price and ATR per pair, latest close
- Performance: total closed positions, cumulative PnL %, win/loss ratio, per-close PnL, cumulative PnL over time, open positions table
- System: seconds since last successful session (thresholded), paused/running state, OHLC ingestion rate
- Sessions: sessions per hour by status, 24h failure rate, recent sessions table, last session log

Every scheduler tick writes one row to the `sessions` table (also created by migration `20260512_01`) capturing start/end timestamps, completion status, the balance snapshot, per-pair market data (price/ATR/volatility level), and the log lines emitted during the session — these power the Sessions row of the dashboard.

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

Open `http://localhost:3000` in a browser. The `BoTC Overview` dashboard should render. With one full session under the bot's belt, panel 10 ("Seconds since last successful session") must show a value < `SLEEPING_INTERVAL + 30`, and Row 4 must show at least one row in "Recent sessions" with `status = ok` and a populated log preview.

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

1. `feat(observability): sessions table, per-session telemetry, and grafana_reader role`
2. `test(db): grafana_reader integration test and CI wiring`
3. `feat(compose): add grafana service with named volume and provisioning mounts`
4. `feat(grafana): provision PostgreSQL datasource via filesystem`
5. `feat(grafana): provision BoTC Overview dashboard (market + performance + system)`
6. `docs(readme): document Grafana observability layer and provisioning workflow`

The PR can be opened after commit 6. The dashboard screenshot may land as a follow-up commit once the first deploy produces real panel data.

---

## Acceptance checklist

Run all of these before opening the PR:

- [ ] `grep -rn "create_session\|finalize_session" core/` returns matches in `core/scheduler.py` and `core/database.py`.
- [ ] `docker compose -f docker-compose.test.yml run --rm test pytest tests/unit/core/test_scheduler.py -v` passes (all three session-recording tests).
- [ ] `scripts/migrations/versions/20260512_01_phase8_observability.py` exists, declares `down_revision = "20260414_01"`, creates the `sessions` table, and includes `sessions` in `GRAFANA_TABLES` per Step 1.2.
- [ ] `docker compose -f docker-compose.test.yml run --rm -e POSTGRES_PASSWORD=botc -e GRAFANA_DB_PASSWORD=ci_grafana_password test alembic upgrade head` exits `0`.
- [ ] `docker compose -f docker-compose.test.yml run --rm -e POSTGRES_PASSWORD=botc -e GRAFANA_DB_PASSWORD=ci_grafana_password -e RUN_DB_INTEGRATION=true test pytest tests/integration/test_grafana_role.py -v` passes both tests (the `SELECT 1 FROM sessions` case included).
- [ ] `docker compose -f docker-compose.yml config | grep -A1 "container_name: botc-grafana"` shows the grafana service.
- [ ] `docker compose -f docker-compose.yml -f docker-compose.prod.yml config` still parses cleanly (grafana inherits unchanged).
- [ ] `grafana/provisioning/datasources/postgres.yaml` references `uid: botc-postgres` and `user: grafana_reader`.
- [ ] `grafana/provisioning/dashboards/botc.yaml` has `allowUiUpdates: false`.
- [ ] `python -c "import json; d = json.load(open('grafana/dashboards/botc.json')); assert d['uid'] == 'botc-overview'; assert len(d['panels']) >= 16"` exits `0`.
- [ ] `.env.example` documents `GRAFANA_DB_PASSWORD`, `GF_SECURITY_ADMIN_USER`, `GF_SECURITY_ADMIN_PASSWORD`.
- [ ] `.github/workflows/ci.yml` integration job passes `GRAFANA_DB_PASSWORD=ci_grafana_password` to both the migration step and the test step.
- [ ] `docker compose up -d` followed by 90s wait then `curl http://localhost:3000/api/health` returns `200`.
- [ ] `curl http://localhost:3000/api/dashboards/uid/botc-overview | jq -r '.dashboard.title'` returns `BoTC Overview`.
- [ ] After one full scheduler session, `SELECT status, balance IS NOT NULL AS has_balance, jsonb_array_length(log_messages) > 0 AS has_logs FROM sessions ORDER BY id DESC LIMIT 1;` returns `('ok', true, true)`.
- [ ] After one full scheduler session, panel 10's query (`SELECT EXTRACT(EPOCH FROM (now() - MAX(ended_at))) FROM sessions WHERE status = 'ok'`) returns a value below `SLEEPING_INTERVAL + 30`.
- [ ] `docker compose restart grafana` followed by re-reading the dashboard returns the same `BoTC Overview` payload (proves provisioning re-applies).
- [ ] `README.md` has an Observability — Grafana section listing the three provisioning files, the `sessions` table, and the screenshot reference (screenshot file may be a follow-up commit).

---

## Non-goals for this phase

Explicitly out of scope — do not add any of these:

- **Alerting** (Grafana alerts, contact points, notification policies). Telegram already covers operational notifications; duplicating them through Grafana is a later concern.
- **Loki, Prometheus, OpenTelemetry, or any new metrics/logs pipeline.** The four existing Postgres tables plus the new `sessions` table are the entire observability data surface for Phase 8.
- **A dedicated error-rate panel.** That would require structured event logging, which is a Phase 8.x or later effort.
- **Multi-dashboard organisation** (folders, multiple JSON files per pair, per-strategy dashboards). One overview dashboard is enough until there is a second consumer.
- **Public exposure of the Grafana port.** Bound to `127.0.0.1` only; HTTPS / reverse proxy / SSO are not in scope.
- **Grafana plugin installation.** `GF_INSTALL_PLUGINS` is explicitly empty.
- **A CHANGELOG entry** (Phase 9 introduces the changelog).
- **Per-event tables** (session_events, session_pair_snapshots, etc.). The single `sessions` table with JSONB columns is intentional — one row per tick is the unit of telemetry; finer normalization can come later if a panel needs it.
- **Structured retention / archival of `sessions`.** Rows accumulate forever in this phase. A retention policy (e.g. drop sessions older than 90 days, or roll old rows into an aggregate table) is a follow-up if the table size becomes a concern.
- **Refactoring `core/scheduler.py`** beyond the session-tracking changes documented in Step 1.5. The exception-handling audit happened in Phase 6.
- **A `down`-tested migration.** `downgrade()` is written for completeness, but rolling back `20260512_01` on a live system would drop the `sessions` table and the read-only role together — a deliberate one-way door at this stage.
