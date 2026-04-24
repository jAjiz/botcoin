# Phase 5 – FastAPI (bot + API unified) + Telegram Service

## Context

- Branch: `feature/phase-5-fastapi` (already created)
- Prior phases delivered: Docker (Phase 1), APScheduler (Phase 2), pytest (Phase 3), PostgreSQL via SQLAlchemy + Alembic (Phase 4)
- Relevant files to read before starting:
  - `ROADMAP.md` — Phase 5 scope (authoritative)
  - `main.py` — scheduler entrypoint; to be reworked as the uvicorn entrypoint
  - `core/runtime.py` — in-memory cache, kept as-is with two new timing fields
  - `core/database.py` — SQLAlchemy models + DAL (no schema changes in this phase)
  - `core/logging.py` — `to_telegram` path, rewired over HTTP
  - `services/telegram.py` — to be rewritten as an independent FastAPI service
  - `docker-compose.yml`, `docker-compose.test.yml` — service definitions
  - `tests/unit/`, `tests/integration/` — test conventions
- Architectural decision: the bot and the API share a process. Running them as separate containers would not deliver independent scaling, independent deploys, or fault isolation for this single-user bot, but would force `core/runtime` state into Postgres solely to bridge containers. Telegram stays separate because it owns an external long-lived connection.

## Target architecture

```
┌──────────────────────────────────────┐   SQLAlchemy     ┌──────────────┐
│  botc (uvicorn)                      │ ───────────────► │  postgres    │
│  ┌────────────────────────────────┐  │ ◄─────────────── │              │
│  │ FastAPI app                    │  │                  └──────────────┘
│  │   routes (async/sync, I/O)     │  │
│  │   global exception handler     │  │
│  │   lifespan:                    │  │
│  │     AsyncIOScheduler           │  │
│  │       └─ ThreadPoolExecutor    │  │
│  │            (trading_session)   │  │
│  └────────────────────────────────┘  │
│  core.runtime (in-memory, locked)    │
└──────┬───────────────────────────────┘
       │                  ▲
       │ POST /notify     │ GET /market /positions /balance /status
       │                  │ POST /control/pause /control/resume
       ▼                  │
┌────────────────────────────┐
│  telegram (uvicorn)        │
│  ┌──────────────────────┐  │
│  │ FastAPI app          │  │
│  │   POST /notify       │  │
│  │   lifespan:          │  │
│  │     PTB polling      │  │
│  │   command handlers → │──┘ (httpx → botc:8000)
│  └──────────────────────┘
└──────────┬─────────────────┘
           │
           ▼
   Telegram Bot API (external)
```

Two containers + Postgres. The bot never imports Telegram code. The scheduler runs in its own thread so API handlers cannot stall it; conversely, API handler errors cannot propagate into scheduler execution.

---

## Step 0 — Dependencies

Add to `requirements.txt`:

```
fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
httpx>=0.27,<1.0
```

(`pydantic` is already installed transitively via FastAPI — no need to pin unless the test suite requires a specific minor.)

Rebuild the image so subsequent steps run in Docker: `docker compose build`.

---

## Step 1 — Convert the scheduler

### 1.1 Replace `BlockingScheduler` with `AsyncIOScheduler`

The bot no longer owns the main thread — uvicorn does. Move the scheduler into a FastAPI `lifespan` context and use `AsyncIOScheduler` so it integrates with the asyncio loop.

**Fault-isolation requirement:** `trading_session()` is synchronous and performs blocking HTTP I/O (Kraken) and blocking DB I/O (SQLAlchemy). Running it directly on the asyncio loop would stall API handlers. Configure APScheduler with a dedicated `ThreadPoolExecutor` so sessions run in a background thread:

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.triggers.interval import IntervalTrigger

scheduler = AsyncIOScheduler(
    executors={"default": ThreadPoolExecutor(max_workers=1)},
    job_defaults={"max_instances": 1, "coalesce": True},
)
scheduler.add_job(
    trading_session,
    trigger=IntervalTrigger(seconds=SLEEPING_INTERVAL),
    next_run_time=datetime.now(),
)
```

Keep `max_instances=1` so overlapping sessions remain impossible.

### 1.2 Move `trading_session` out of `main.py`

Extract `trading_session()` (and its helpers `check_closed_position`, `check_open_position`, `update_trailing_state`, `call_with_retry`) into a new module `core/scheduler.py`. The rationale: `main.py` becomes a thin uvicorn entrypoint, and FastAPI's lifespan imports the job function from a clearly named module.

### 1.3 Delete manual signal handling

Uvicorn already installs `SIGTERM`/`SIGINT` handlers that trigger `lifespan` shutdown. The lifespan exit branch must call `scheduler.shutdown(wait=True)` to preserve the Phase 2 guarantee that the current job completes before exit. Remove the `signal.signal(...)` setup currently in `main.py`.

---

## Step 2 — `last_run_at` timing field in `core/runtime`

Add one module-level timestamp and a helper, guarded by the existing `_lock`:

```python
_shared_data["last_run_at"] = None

def update_last_run_at(last_run_at):
    with _lock:
        _shared_data["last_run_at"] = last_run_at

def get_last_run_at():
    with _lock:
        return _shared_data["last_run_at"]
```

Call `update_last_run_at()` at the end of `trading_session()`:

```python
runtime.update_last_run_at(now_utc())
```

`next_run_at` is **not tracked**. Consumers that need the next fire time can derive it from `last_run_at + SLEEPING_INTERVAL` — adding it to the `/status` payload would just duplicate data the caller already has. Before the first tick completes, `last_run_at` is `null` in the response; callers must handle that.

**Important:** state lives in memory only. After a restart, `last_run_at` resets to `None` until the first session completes (seconds). This is an accepted trade-off — Phase 4 already persists the *trading* state (trailing state, closed positions, bot control flags) durably; runtime metadata is transient by design.

---

## Step 2.1 — Harden `core/runtime`

Two existing issues become real bugs once the API threadpool reads from `core/runtime` alongside the scheduler thread. Fix both while the module is already being touched.

### 2.1.1 Remove the `trailing_state` in-memory mirror

`update_trailing_state` / `get_trailing_state` exist only as a read path for the old in-process Telegram thread. In Phase 5, `/positions` reads `db.load_trailing_state(pair)` directly from Postgres — the mirror has no remaining consumer.

Keeping it would create two sources of truth: the scheduler persists per-pair inside the loop (`main.py:88` → `db.save_trailing_state`), and then `runtime.update_trailing_state(trailing_state)` at `main.py:90` re-broadcasts the bulk dict into memory. A crash between those lines leaves the mirror disagreeing with the DB.

Required edits:

- Delete `update_trailing_state` and `get_trailing_state` from `core/runtime.py`.
- Remove the `"trailing_state": {}` entry from `_shared_data`.
- Remove `import copy` (becomes unused).
- Delete the `runtime.update_trailing_state(trailing_state)` call at `main.py:90` (already moves to `core/scheduler.py` per Step 1.2 — drop it during the extract).
- Grep for any remaining import: `grep -rn "runtime.get_trailing_state\|runtime.update_trailing_state" .` must return nothing.

### 2.1.2 Return copies from getters

```python
def get_last_balance():
    with _lock:
        return _shared_data["last_balance"]          # ← leaks the live dict
```

The lock protects the dict *lookup*; the caller then reads (and could mutate) the returned dict outside the lock. Today this is merely latent — two threads touch the cache. In Phase 5 the API threadpool reads it as well, and a handler or Pydantic serializer touching the dict mid-write can corrupt state or produce torn reads.

Fix: return a shallow copy. Values are primitives (`float`, `str`), so `dict(...)` is sufficient — no `deepcopy` needed.

```python
def get_last_balance():
    with _lock:
        return dict(_shared_data["last_balance"])

def get_pair_data(pair):
    with _lock:
        return dict(_shared_data["pairs_data"].get(pair, {}))
```

`get_last_run_at` already returns a `datetime` (immutable), so no change.

### 2.1.3 Explicitly out of scope

Leave these for Phase 6 (linting / type safety) to avoid scope creep here:

- Flattening `_shared_data` into individual module-level variables.
- Replacing the `None`-means-"don't-update" parameter style in `update_pair_data`.
- Adding type hints.

### 2.1.4 Commit

Fold these changes into a dedicated commit before the API work begins:

> `refactor(runtime): drop trailing_state mirror, return copies from getters`

Add two unit tests under `tests/unit/core/test_runtime.py` (create if missing):

- `test_get_last_balance_returns_copy` — mutate the returned dict, assert the internal state is unchanged on the next call.
- `test_get_pair_data_returns_copy` — same pattern.

---

## Step 3 — FastAPI `api` package

New layout (the app lives inside `api/`; `main.py` at the repo root becomes the uvicorn entrypoint module):

```
api/
├── __init__.py
├── app.py           # FastAPI app factory, lifespan, global exception handler
├── routes/
│   ├── __init__.py
│   ├── market.py
│   ├── positions.py
│   ├── balance.py
│   ├── status.py
│   └── control.py
└── schemas.py       # Pydantic v2 response models
main.py              # `from api.app import app` — uvicorn target
```

### 3.1 `api/app.py`

```python
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.triggers.interval import IntervalTrigger

import core.logging as logging
import core.database as db
import core.runtime as runtime
from core.config import SLEEPING_INTERVAL, TELEGRAM_ENABLED
from core.scheduler import trading_session
from core.utils import now_utc

scheduler: AsyncIOScheduler | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    if not db.check_database_connection():
        raise RuntimeError("Cannot connect to PostgreSQL")

    scheduler = AsyncIOScheduler(
        executors={"default": ThreadPoolExecutor(max_workers=1)},
        job_defaults={"max_instances": 1, "coalesce": True},
    )
    scheduler.add_job(
        trading_session,
        trigger=IntervalTrigger(seconds=SLEEPING_INTERVAL),
        next_run_time=datetime.now(),
    )
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=True)
        logging.info("Scheduler stopped.")

app = FastAPI(title="BoTC API", version="0.1.0", lifespan=lifespan)

@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    logging.error(f"Unhandled error in {request.method} {request.url.path}: {exc}")
    return JSONResponse(status_code=500, content={"detail": "internal error"})

from api.routes import market, positions, balance, status, control
for r in (market, positions, balance, status, control):
    app.include_router(r.router)
```

### 3.2 Fault-isolation rules (enforced in code review)

- **Routes are `def`, not `async def`, whenever they call SQLAlchemy or acquire `runtime._lock`.** FastAPI auto-offloads sync handlers to a threadpool, so blocking calls never sit on the event loop.
- **No route holds `runtime._lock` across I/O.** The existing `core.runtime` getters copy-out under the lock and return — preserve that pattern.
- **The global exception handler is the last line of defence.** Any handler that raises returns a `500`; nothing reaches uvicorn's default error path or affects the scheduler thread.
- **All outbound HTTP calls (from Telegram's side) set a short timeout** (≤ 5 s) via `httpx` — prevents a hung remote from stalling a request.

### 3.3 Endpoint contracts

| Method + Path             | Response shape (JSON)                                                                                  | Data source                                                            |
| ------------------------- | ------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| `GET /market`             | `[{pair, last_price, atr, volatility_level}, ...]` for every configured pair                           | `runtime.get_pair_data(pair)` per `PAIRS.keys()`                       |
| `GET /market/{pair}`      | Single market object; `404` if `pair not in PAIRS`; fields may be null before first tick               | `runtime.get_pair_data(pair)`                                          |
| `GET /positions`          | `{pair: {…position fields…, estimated_pnl_percent: float \| null} \| null, ...}` for every configured pair | `db.load_trailing_state(pair)` + `runtime.get_pair_data(pair)` for PnL |
| `GET /positions/{pair}`   | Single position object or `{pair, position: null}`; `404` on unknown pair                              | Same as above                                                          |
| `GET /balance`            | `{balance: {asset: amount}}`                                                                           | `runtime.get_last_balance()`                                           |
| `GET /status`             | `{paused: bool, last_run_at: iso8601 \| null}`                                                         | `db.get_bot_paused()` + `runtime.get_last_run_at()`                    |
| `POST /control/pause`     | `{paused: true, updated_by: str \| null}`; idempotent (no-op if already paused)                         | `db.set_bot_paused(True, updated_by=...)`                              |
| `POST /control/resume`    | `{paused: false, updated_by: str \| null}`; idempotent                                                  | `db.set_bot_paused(False, updated_by=...)`                             |

POST bodies accept `{"updated_by": "telegram"}` (optional). Use Pydantic models in `api/schemas.py`; do not hand-roll dicts.

### 3.4 PnL calculation

In `api/routes/positions.py`, replicate the logic from `services/telegram.py:172–187` in one helper:

```python
def estimated_pnl_percent(pos, last_price):
    trailing_price = pos.get("trailing_price")
    stop_price = pos.get("stop_price")
    if trailing_price is None or stop_price is None:
        return None
    entry_price = pos["entry_price"]
    if pos["side"] == "sell":
        return (stop_price - entry_price) / entry_price * 100
    return (entry_price - stop_price) / entry_price * 100
```

The API owns this calculation from now on; do not duplicate it in the Telegram service.

### 3.5 `main.py`

Reduce to the uvicorn target:

```python
from core.validation import validate_config
from api.app import app  # noqa: F401  (imported so `uvicorn main:app` works)

if not validate_config():
    raise SystemExit(1)
```

All pre-existing `trading_session` logic now lives in `core/scheduler.py`. All lifecycle management lives in `api/app.py`'s lifespan.

---

## Step 4 — Telegram as an independent FastAPI service

Convert `services/telegram.py` into a package. The bot **must not** import it.

### 4.1 Structure

```
services/
└── telegram/
    ├── __init__.py
    ├── app.py        # FastAPI app, lifespan (start/stop PTB Application), /notify
    ├── polling.py    # python-telegram-bot ApplicationBuilder + command handlers
    └── client.py     # module-level httpx.AsyncClient calling API_BASE_URL
```

### 4.2 `/notify` endpoint

```python
from typing import Literal
from pydantic import BaseModel

class NotifyRequest(BaseModel):
    message: str
    level: Literal["info", "warning", "error"] = "info"

PREFIX = {"info": "", "warning": "⚠️ ", "error": "❌ "}

@app.post("/notify", status_code=202)
async def notify(req: NotifyRequest):
    try:
        await tg_app.bot.send_message(
            chat_id=TELEGRAM_USER_ID,
            text=PREFIX[req.level] + req.message,
        )
    except Exception as e:
        logging.error(f"Telegram send failed: {e}")
    return {"accepted": True}
```

Return `202` so the bot can treat the call as fire-and-forget. Never raise — always `accepted: true`.

### 4.3 Lifespan owns the polling loop

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling(poll_interval=TELEGRAM_POLL_INTERVAL)
    try:
        yield
    finally:
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
```

This replaces the threading + `asyncio.new_event_loop()` gymnastics in the current implementation. PTB and FastAPI share the single uvicorn event loop.

### 4.4 Command handlers

Rewrite handlers in `services/telegram/polling.py`. Each handler:
- Calls `client.py` (no `core.database`, no `core.runtime`, no in-process reads).
- Catches request errors and replies with a friendly error message; never raises.

Mapping:

| Telegram command   | Backend call                                                              | Formatting                                                   |
| ------------------ | ------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `/status`          | `GET /status`                                                             | Reuse the text template from current `status_command`        |
| `/pause`           | `POST /control/pause` with body `{"updated_by": "telegram"}`              | Reply with returned `paused` state                           |
| `/resume`          | `POST /control/resume` with body `{"updated_by": "telegram"}`             | Same                                                         |
| `/market [pair]`   | `GET /market` (or `/market/{pair}`) + `GET /balance`                      | Reuse template from `market_command` (telegram.py:116–130)   |
| `/positions [pair]`| `GET /positions` (or `/positions/{pair}`)                                 | Reuse template from `positions_command` (telegram.py:148–189); drop local PnL calc (the API returns it) |

### 4.5 Entrypoint

Run with `uvicorn services.telegram.app:app --host 0.0.0.0 --port 8001`.

---

## Step 5 — Rewire `core/logging.py`

Replace the in-process Telegram call with an HTTP POST. Requirements:
- Short connect/read timeout (2 s) — a hung Telegram service never stalls a scheduler tick.
- Swallow all exceptions — log locally, never raise.
- No import of `services.telegram` anywhere in `core/`, `main.py`, `api/`, `trading/`, `exchange/`.

```python
import os, logging, httpx
from logging.handlers import TimedRotatingFileHandler
from core.config import TELEGRAM_ENABLED

# (file/stream handlers unchanged)

TELEGRAM_SERVICE_URL = os.getenv("TELEGRAM_SERVICE_URL")  # e.g. http://telegram:8001

def _notify(level: str, msg: str) -> None:
    if not TELEGRAM_ENABLED or not TELEGRAM_SERVICE_URL:
        return
    try:
        httpx.post(
            f"{TELEGRAM_SERVICE_URL}/notify",
            json={"message": msg, "level": level},
            timeout=2.0,
        )
    except Exception as e:
        logging.warning(f"Telegram notify failed: {e}")

def info(msg, to_telegram=False):
    logging.info(msg)
    if to_telegram:
        _notify("info", msg)

def warning(msg, to_telegram=False):
    logging.warning(msg)
    if to_telegram:
        _notify("warning", msg)

def error(msg, to_telegram=False):
    logging.error(msg)
    if to_telegram:
        _notify("error", msg)
```

Delete the `import services.telegram as telegram` line at the top. The function signatures are unchanged, so every call site in the codebase keeps working.

---

## Step 6 — Docker Compose

### 6.1 `docker-compose.yml`

Two application services plus Postgres (promoted out of the `data` profile — both services require it unconditionally):

```yaml
services:
  botc:
    build: {context: ., dockerfile: Dockerfile}
    container_name: botc
    restart: unless-stopped
    env_file: [.env]
    environment:
      TELEGRAM_SERVICE_URL: http://telegram:8001
    depends_on:
      postgres: {condition: service_healthy}
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - ./logs:/app/logs
    networks: [botc_backend]
    command: ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

  telegram:
    build: {context: ., dockerfile: Dockerfile}
    container_name: botc-telegram
    restart: unless-stopped
    env_file: [.env]
    environment:
      API_BASE_URL: http://botc:8000
    depends_on:
      botc: {condition: service_started}
    ports:
      - "127.0.0.1:8001:8001"
    networks: [botc_backend]
    command: ["uvicorn", "services.telegram.app:app", "--host", "0.0.0.0", "--port", "8001"]

  postgres:
    # unchanged except: remove `profiles: ["data"]`
```

### 6.2 `.env.example`

Add:

```
API_BASE_URL=http://botc:8000
TELEGRAM_SERVICE_URL=http://telegram:8001
```

### 6.3 `docker-compose.test.yml`

The `test` service still runs `pytest` against a live Postgres. No new services are required — integration tests spin up the API using FastAPI's `TestClient` in-process and stub Telegram via monkeypatching.

---

## Step 7 — Tests

### 7.1 New unit tests (`tests/unit/api/`)

Use `fastapi.testclient.TestClient`. Skip the lifespan for most tests by using `TestClient(app)` with `raise_server_exceptions=False` where appropriate, or instantiate routes directly.

- `test_market_routes.py` — monkeypatch `core.runtime.get_pair_data`; assert list shape, `404` on unknown pair.
- `test_positions_routes.py` — monkeypatch `core.database.load_trailing_state` + `core.runtime.get_pair_data`; three cases: no position, pending activation (PnL null), trailing active (PnL computed).
- `test_balance_routes.py` — monkeypatch `core.runtime.get_last_balance`.
- `test_status_routes.py` — monkeypatch `core.database.get_bot_paused` + `core.runtime.get_last_run_at`; assert the response shape and that `last_run_at` is `null` before the first tick.
- `test_control_routes.py` — monkeypatch `core.database.set_bot_paused`; assert `updated_by` is passed through and idempotency holds.
- `test_exception_handler.py` — add a temporary route that raises; assert `500` + no exception propagated.

### 7.2 New unit tests (`tests/unit/telegram/`)

- `test_notify_route.py` — monkeypatch `tg_app.bot.send_message`; verify prefix mapping for `info`/`warning`/`error`; verify `/notify` returns `202` even when `send_message` raises.
- `test_handlers.py` — monkeypatch `httpx.AsyncClient` used by `client.py`; assert each command issues the correct HTTP call and formats the response.

### 7.3 Updated unit tests

- `tests/unit/core/test_logging.py` (create if missing) — monkeypatch `httpx.post`; assert URL + payload shape; assert exceptions raised by `httpx.post` are swallowed (no re-raise).
- Any existing test importing `services.telegram` must be updated — the module is now a package with a different shape.

### 7.4 New integration tests (`tests/integration/api/`)

Marked with `@pytest.mark.integration`:

- `test_status_flow.py` — start the app via `TestClient` with a live Postgres; `POST /control/pause` → `GET /status` (paused true) → `POST /control/resume` → `GET /status` (paused false).
- `test_market_after_tick.py` — manually invoke `core.scheduler.trading_session` once inside the test (with real or stubbed Kraken), then `GET /market` and assert populated data.

### 7.5 Coverage gate

Update `pytest.ini` to add `api/` and `services/telegram/` to the `--cov` scope. Keep the 80% threshold.

---

## Step 8 — Documentation

- Update `README.md` Quick Start: two-container compose, Swagger at `http://localhost:8000/docs`.
- Add a short "Service architecture" paragraph referencing the diagram in this plan.
- No `CHANGELOG.md` entry yet — that is Phase 8.

---

## Execution order (commits)

Each bullet is one focused commit. Run `pytest tests/unit` inside Docker after each commit.

1. `feat(deps): add fastapi, uvicorn, httpx`
2. `refactor(scheduler): extract trading_session into core/scheduler.py`
3. `refactor(main): swap BlockingScheduler for AsyncIOScheduler + threadpool executor`
4. `feat(runtime): add last_run_at field`
5. `refactor(runtime): drop trailing_state mirror, return copies from getters`
6. `feat(api): FastAPI app skeleton with lifespan + global exception handler`
7. `feat(api): /market, /balance endpoints`
8. `feat(api): /positions with PnL, /status`
9. `feat(api): /control/pause, /control/resume`
10. `feat(telegram): FastAPI service with POST /notify + lifespan polling`
11. `refactor(telegram): commands call the API via httpx`
12. `refactor(logging): POST to telegram /notify over HTTP`
13. `feat(compose): promote postgres, run botc via uvicorn, add telegram service`
14. `test(api,telegram): unit + integration coverage`
15. `docs(readme): two-container architecture + Swagger`

---

## Acceptance checklist

Run all of these before opening the PR:

- [ ] `docker compose build` succeeds.
- [ ] `docker compose up` starts `postgres`, `botc`, `telegram` and all stay healthy for 5 minutes.
- [ ] `curl http://localhost:8000/docs` returns the Swagger UI.
- [ ] `curl http://localhost:8000/status` returns real `last_run_at` after the first session.
- [ ] `curl http://localhost:8000/market` returns one entry per configured pair after one tick.
- [ ] `POST /control/pause` flips `GET /status` → `paused: true`; `POST /control/resume` flips it back.
- [ ] Issuing `/pause` via Telegram produces an HTTP request in `docker logs botc` (not just in `botc-telegram`).
- [ ] Forcing an error in `trading_session` produces a Telegram message and a matching `/notify` log line in `botc-telegram`.
- [ ] A route that raises unexpectedly returns `500` — the scheduler keeps firing (verify via `last_run_at` advancing after the error).
- [ ] `grep -rn "import services.telegram" main.py core/ api/ trading/ exchange/` returns nothing.
- [ ] `grep -rn "BlockingScheduler\|signal.signal" main.py core/` returns nothing.
- [ ] `docker compose -f docker-compose.test.yml run --rm test pytest tests/unit` passes with ≥ 80 % coverage across `core`, `trading`, `exchange`, `api`, `services/telegram`.
- [ ] `docker compose -f docker-compose.test.yml run --rm test pytest tests/integration` passes when Kraken credentials are present.

---

## Non-goals for this phase

Explicitly out of scope — do not add any of these:

- Splitting the bot and the API into separate containers (the reason this plan exists in its current shape).
- Persisting runtime state (`last_price`, `atr`, `balance`, `last_run_at`) to Postgres — the in-memory cache is sufficient; API consumers tolerate a short post-restart warm-up.
- Authentication on the API — services run on a private Docker network; auth is a later-phase concern.
- Persistent notification queue or retry logic for failed `/notify` calls — best-effort is correct.
- Replacing `python-telegram-bot` polling with webhooks.
- WebSocket / SSE push from the API.
- Linting/formatting (Phase 6) or CI changes (Phase 7).
