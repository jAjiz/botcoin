# Phase 6 – Code Quality: Linting & Type Safety

## Context

- Branch: `feature/phase-6-code-quality` (already created)
- Prior phases delivered: Docker (Phase 1), APScheduler (Phase 2), pytest (Phase 3), PostgreSQL via SQLAlchemy + Alembic (Phase 4), FastAPI + Telegram split (Phase 5).
- Files added in Phase 4/5 (`core/database.py`, `api/**`, `api/schemas.py`, `services/telegram/**`) already carry argument and return-type annotations and are the **reference style** for this phase. Extend the same conventions to the older modules; do not reformat the new ones beyond what `ruff format` requires.
- Relevant files to read before starting:
  - `ROADMAP.md` — Phase 6 scope (authoritative)
  - `pytest.ini`, `.coveragerc` — to be folded into `pyproject.toml`
  - `requirements-dev.txt` — where `ruff` is added
  - `core/{config,runtime,scheduler,logging,validation,utils}.py` — primary type-hint targets
  - `exchange/kraken.py` — primary type-hint + error-handling target
  - `trading/{positions,parameters,inventory,market_analyzer}.py` — primary type-hint target
  - `services/telegram/{app,polling}.py` — finish the type hints (most are present, gaps remain)
  - `tests/unit/conftest.py` — preserve fixture signatures when adding hints
- Architectural decisions:
  - **No public API changes.** Function signatures gain types but keep their names, parameters, defaults, and behaviour. Phase 6 is a quality-only refactor; do not introduce features or alter contracts.
  - **`pyproject.toml` becomes the single source of truth** for lint, format, and test configuration. `pytest.ini` and `.coveragerc` are deleted at the end.
  - **No new tooling beyond `ruff`.** `mypy` / `pyright` are out of scope — `ruff`'s rule sets plus annotated signatures give us the cost/benefit ratio we want without dragging in a separate type-checker pipeline.

## Target outcome

```
$ docker compose -f docker-compose.test.yml run --rm test ruff check .
All checks passed!

$ docker compose -f docker-compose.test.yml run --rm test ruff format --check .
N files already formatted

$ docker compose -f docker-compose.test.yml run --rm test pytest
... 80%+ coverage gate met, all tests pass.
```

Every public function in `core/`, `exchange/`, `trading/`, `services/telegram/`, `api/`, and `scripts/` carries argument and return-type annotations. `try / except Exception` blocks have an explicit role (swallow vs propagate) documented by the surrounding code, not by accident.

---

## Step 0 — Dependencies & tooling

### 0.1 Add `ruff` to `requirements-dev.txt`

```
ruff==0.14.0
```

(Pin to a known version; the project pins everything else exactly. Bump as a separate concern later.)

### 0.2 Rebuild the dev image

```
docker compose -f docker-compose.test.yml build
```

All subsequent `ruff` and `pytest` invocations in this plan run inside the `test` service. Do not install `ruff` on the host.

---

## Step 1 — `pyproject.toml` as the single source of truth

Create `pyproject.toml` at the repository root. It serves three roles: project metadata stub, `ruff` config, and `pytest`/coverage config (replacing `pytest.ini` and `.coveragerc`).

```toml
[project]
name = "botc"
version = "0.1.0"
description = "BoTCoin V2 — managed trading bot with FastAPI control surface."
requires-python = ">=3.12"

[tool.ruff]
line-length = 120
target-version = "py312"
extend-exclude = ["venv", "logs", "scripts/migrations/versions", "__pycache__"]

[tool.ruff.lint]
# E/W/F = pycodestyle + pyflakes; I = import sorting; UP = pyupgrade;
# B = bugbear; SIM = simplify; RUF = ruff-specific.
select = ["E", "W", "F", "I", "UP", "B", "SIM", "RUF"]
ignore = [
    "E501",  # line length is enforced by `ruff format`, not the linter.
]

[tool.ruff.lint.per-file-ignores]
# Analysis scripts use sys.argv parsing and verbose prints; relax the rules
# instead of forcing them into a different shape.
"trading/backtest.py" = ["B", "SIM"]
"trading/optimize_params.py" = ["B", "SIM"]
"core/utils.py" = ["B"]  # the print_* helpers intentionally use bare prints.
"tests/**" = ["B", "SIM"]  # fixtures and parametrize expressions trip rules.

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
addopts = "-ra --strict-markers --cov --cov-report=term-missing --cov-fail-under=80"
markers = [
    "unit: Unit tests with no network/external APIs.",
    "integration: Integration tests that may call external services.",
    "asyncio: Marks tests as async (pytest-asyncio).",
]

[tool.coverage.run]
source = ["api", "core", "exchange", "services", "trading"]
omit = [
    "trading/backtest.py",
    "trading/optimize_params.py",
    "core/runtime.py",
    "core/scheduler.py",
]
```

After committing the file:

- Delete `pytest.ini`.
- Delete `.coveragerc`.
- Verify the suite still runs: `docker compose -f docker-compose.test.yml run --rm test pytest tests/unit`.

**Commit:** `chore(tooling): add pyproject.toml; remove pytest.ini and .coveragerc`.

---

## Step 2 — Format-only commit

Before any logical change, run `ruff format` once and commit the result alone. This keeps reviewers from having to scan whitespace-only diffs in subsequent commits.

```
docker compose -f docker-compose.test.yml run --rm test ruff format .
```

Verify the test suite still passes inside Docker, then commit:

**Commit:** `style: apply ruff format to the entire repository`.

If `ruff format` produces unexpected diffs in `scripts/migrations/versions/*` (Alembic generates these), confirm `extend-exclude` in `pyproject.toml` covers them — Alembic-generated migrations should not be reformatted.

---

## Step 3 — Lint clean-up (no behaviour changes)

Run `ruff check .` and resolve every reported issue **without changing behaviour**. Most fixes are mechanical: unused imports, `dict()` → literal, `f-string` without placeholders, etc.

```
docker compose -f docker-compose.test.yml run --rm test ruff check . --fix
```

Then re-run without `--fix` and inspect remaining diagnostics manually. Cases worth attention:

- **`F401` unused imports** — usually safe to delete; check `__init__.py` files for re-export intent first.
- **`B904` raise from** — when re-raising inside `except`, use `raise ... from e` (matters in `core/database.py`, `exchange/kraken.py`).
- **`SIM117` combine `with`** — leave alone if the inner block is non-trivial.
- **`UP` pyupgrade** — accept the suggestions (`Optional[X]` → `X | None`, `List[X]` → `list[X]`, etc.). Already done in newer modules; this aligns the rest.

Run the full unit suite after the fixes; nothing functional changed, so it must still pass.

**Commit:** `style(ruff): resolve lint diagnostics across legacy modules`.

---

## Step 4 — Type annotations

Treat each module below as one focused commit so reviewers can trace the diff. After each commit, run `pytest tests/unit` and `ruff check .` inside Docker; both must pass before moving on.

The reference style (already used in `core/database.py`, `api/schemas.py`, `services/telegram/polling.py`) is:

- `X | None` rather than `Optional[X]`.
- `dict[str, Any]` rather than `Dict[str, Any]`.
- Explicit `-> None` on functions that do not return.
- `Decimal | float | int` etc. only at module boundaries; internally pick one type.
- `from __future__ import annotations` is **not** required — Python 3.12 is the floor — but if a module already has it, keep it.

The modules and the public surface to annotate:

### 4.1 `core/runtime.py`

```python
from datetime import datetime
from typing import Any

def update_balance(balance: dict[str, Any] | None) -> None: ...
def get_last_balance() -> dict[str, Any]: ...
def update_pair_data(
    pair: str,
    price: float | None = None,
    atr: float | None = None,
    volatility_level: str | None = None,
) -> None: ...
def get_pair_data(pair: str) -> dict[str, Any]: ...
def update_last_run_at(last_run_at: datetime) -> None: ...
def get_last_run_at() -> datetime | None: ...
```

The `_shared_data` dict's value types are heterogeneous; `Any` is the honest annotation for now. Do **not** introduce a `TypedDict` here — that is a Phase-7+ refactor.

### 4.2 `core/scheduler.py`

```python
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

def call_with_retry(func: Callable[..., T | None], *args: Any) -> T | None: ...
def trading_session() -> None: ...
def check_closed_position(pair: str, trailing_state: dict[str, Any]) -> bool: ...
def check_open_position(pair: str, trailing_state: dict[str, Any]) -> bool: ...
def _update_trailing_state(
    pair: str,
    current_balance: dict[str, Any],
    last_prices: dict[str, float],
    current_atr: float,
    trailing_state: dict[str, Any],
) -> None: ...
```

Resolve the existing `# TODO: add unit tests …` comment by either opening a tracking issue and removing the comment, or deleting the comment outright if Phase 3's coverage already exercises these paths.

### 4.3 `core/logging.py`

```python
def _notify(level: str, msg: str) -> None: ...
def info(msg: str, to_telegram: bool = False) -> None: ...
def warning(msg: str, to_telegram: bool = False) -> None: ...
def error(msg: str, to_telegram: bool = False) -> None: ...
```

### 4.4 `core/validation.py`

```python
def validate_common_params(errors: list[str]) -> None: ...
def build_and_validate_pairs(errors: list[str]) -> None: ...
def log_configuration_summary() -> None: ...
def validate_config() -> bool: ...  # already present
```

### 4.5 `core/config.py`

Module-level constants are typed implicitly by their initializers. Annotate the helper builders:

```python
def _build_trading_params() -> dict[str, dict[str, dict[str, Any]]]: ...
def _build_asset_allocation() -> dict[str, dict[str, Any]]: ...
def _build_percentiles() -> dict[str, dict[str, float]]: ...
```

### 4.6 `core/utils.py`

`now_utc` already returns `datetime`. Annotate the rest:

```python
def print_pair_argument_error() -> None: ...
def print_statistics(events: list[dict[str, Any]], vol_level: str, title: str) -> None: ...
def print_events_detail(events: list[dict[str, Any]], title: str, vol_level: str | None = None) -> None: ...
def print_structural_noise_results(
    uptrend_events: list[dict[str, Any]],
    downtrend_events: list[dict[str, Any]],
    min_change_pct: float,
    atr_percentiles: dict[str, float],
    show_events: bool = False,
    volatility_level: str | None = None,
) -> None: ...
```

### 4.7 `exchange/kraken.py`

```python
def _wait_rate_limit() -> None: ...
def _query_public_limited(method: str, data: dict[str, Any] | None = None) -> dict[str, Any]: ...
def get_asset_pairs() -> dict[str, Any] | None: ...
def build_pairs_map(pairs_dict: dict[str, dict[str, Any]]) -> None: ...
def get_balance() -> dict[str, str] | None: ...
def get_order_status(order_id: str) -> str | None: ...
def get_last_prices(pairs_dict: dict[str, dict[str, Any]]) -> dict[str, float] | None: ...
def place_limit_order(pair: str, side: str, price: float, volume: float) -> str | None: ...
def fetch_ohlc_data(pair: str, interval: int, since: int | None = None) -> pd.DataFrame | None: ...
```

### 4.8 `trading/positions_manager.py`

```python
def create_position(
    pair: str,
    balance: dict[str, Any],
    last_prices: dict[str, float],
    atr_val: float,
    trailing_state: dict[str, Any],
) -> None: ...
def calculate_activation_price(pair: str, side: str, entry_price: float, atr_val: float) -> float: ...
def update_activation_price(pair: str, pos: dict[str, Any], atr_val: float) -> None: ...
def calculate_stop_price(pair: str, side: str, trailing_price: float, atr_val: float) -> float: ...
def update_stop_price(pair: str, pos: dict[str, Any], trailing_price: float, atr_val: float) -> None: ...
def refresh_position(...) -> bool: ...   # already typed
def close_position(pair: str, pos: dict[str, Any], last_prices: dict[str, float]) -> None: ...
```

### 4.9 `trading/parameters_manager.py`

```python
def calculate_k_stops(pair: str, events: list[dict[str, Any]]) -> dict[str, float | None]: ...
def calculate_trading_parameters(pair: str, infoLog: bool = True) -> None: ...
def get_volatility_level(pair: str, atr_val: float) -> str: ...
def get_k_stop(pair: str, side: str, atr_val: float) -> float | None: ...
```

While here, rename `infoLog` → `info_log` to satisfy `ruff` (`N803`) **only if** `select` includes the `N` rule set; it does not in the config above, so leave the name alone to keep the diff smaller. Note this in the commit body: "naming convention deferred — not enabling `N` in this phase".

### 4.10 `trading/inventory_manager.py`

```python
def calculate_position(
    pair: str,
    balance: dict[str, Any],
    last_prices: dict[str, float],
    trailing_state: dict[str, Any],
    force_side: str | None = None,
) -> tuple[str, float]: ...
```

The other functions are already annotated.

### 4.11 `trading/market_analyzer.py`

`get_current_atr`, `detect_pivots`, `analyze_structural_noise`, `get_args` are typed. Annotate the remaining helper:

```python
def calculate_noise_between_pivots(...) -> dict[str, Any]: ...   # already partly typed
```

Verify the `tuple[tuple[int, str, float, pd.Timestamp], tuple[...]]` annotation is still accurate after any refactor.

### 4.12 `services/telegram/app.py`

```python
async def lifespan(app: FastAPI) -> AsyncIterator[None]: ...
async def notify(req: NotifyRequest, x_api_token: str | None = Header(default=None)) -> dict[str, bool]: ...
```

`tg_app: Application | None = None` (import `Application` from `telegram.ext`) — drop the bare `tg_app = None`.

### 4.13 `services/telegram/polling.py`

The handlers are already annotated. Annotate `build_tg_app`:

```python
def build_tg_app() -> Application: ...
```

### 4.14 `scripts/load_legacy_data.py`

Annotate the entrypoints to match the rest of the codebase. If the script imports from modules with new types, fix any uncovered `mypy`-equivalent shape mismatch by adjusting the call site, not by silencing.

**Commits (one per group):**

- `feat(types): annotate core.runtime`
- `feat(types): annotate core.scheduler`
- `feat(types): annotate core.logging + core.validation + core.config + core.utils`
- `feat(types): annotate exchange.kraken`
- `feat(types): annotate trading package`
- `feat(types): annotate services.telegram.app and finish polling`
- `feat(types): annotate scripts/load_legacy_data`

After each: `ruff check .`, `pytest tests/unit`. Both must pass.

---

## Step 5 — Logging convention normalization

Two patterns coexist today:

```python
# Pattern A (newer, in core/database.py:36 and core/scheduler.py)
import core.logging as logging
logger = logging.logging.getLogger(__name__)   # ← double-indirection
# …
logger.error("…")
logging.info("…", to_telegram=True)            # the project's own wrapper

# Pattern B (older, in exchange/kraken.py, trading/parameters_manager.py)
import logging
logging.error("…")                             # bare stdlib
```

Decision for Phase 6:

- The project's `core/logging` module remains the **only** import for the wrapper that adds `to_telegram=True` notifications.
- Modules that do **not** need Telegram routing keep using stdlib `logging` directly. That is fine — the wrapper is opt-in, not mandatory.
- Replace every `logger = logging.logging.getLogger(__name__)` with `logger = stdlib_logging.getLogger(__name__)` by importing the stdlib explicitly:

```python
import logging as stdlib_logging
import core.logging as logging

logger = stdlib_logging.getLogger(__name__)
```

Yes, two imports — the alias `stdlib_logging` makes the module-name-shadowing problem visible instead of hiding it behind `logging.logging.…`. Apply this only where a `logger = …` line currently exists; do not add it elsewhere.

**Commit:** `refactor(logging): normalize stdlib vs core.logging import alias`.

---

## Step 6 — Collapse repeated boilerplate

Two patterns repeat enough to extract:

### 6.1 `Decimal(str(value))` in `core/database.py`

`_to_decimal` already exists for the nullable case. The non-null path inlines `Decimal(str(...))` ~10 times. Add a sibling helper:

```python
def _to_decimal_required(value: Any) -> Decimal:
    return Decimal(str(value))
```

…and replace the inline calls in `_state_entry_to_trailing_record`, `save_closed_position`, and `save_ohlc_data`. Behaviour is identical; the diff makes the intent explicit.

### 6.2 `try / except Exception / log / return None` in `exchange/kraken.py`

Eight functions follow the same shape:

```python
def get_balance():
    try:
        response = api.query_private("Balance")
        if "error" in response and response["error"]:
            raise Exception(response["error"])
        return response.get("result", {})
    except Exception as e:
        logging.error(f"Error fetching balance: {e}")
        return None
```

Extract a single helper that takes the API call and the human-readable label:

```python
def _safe_call(label: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
    try:
        response = fn()
        if response.get("error"):
            raise KrakenAPIError(response["error"])
        return response.get("result", {})
    except Exception as e:
        logging.error(f"Error fetching {label}: {e}")
        return None
```

Define a narrow `KrakenAPIError` subclass of `Exception` so future code can `except KrakenAPIError:` without catching unrelated errors. Then rewrite the eight functions to thin wrappers:

```python
def get_balance() -> dict[str, str] | None:
    return _safe_call("balance", lambda: api.query_private("Balance"))
```

Functions that need post-processing (`get_last_prices`, `fetch_ohlc_data`, `get_order_status`, `place_limit_order`) keep the post-processing step but route the API call through `_safe_call`. **Do not** change return shapes; integration tests will catch shape regressions.

**Commit:** `refactor(kraken): collapse query/error-log boilerplate into _safe_call`.

### 6.3 What to leave alone

- The `try / except / logger.error / return None` pattern in `core/database.py` reads — collapsing it into a generic helper would obscure the SQLAlchemy session boundaries. Keep as-is.
- `_state_entry_to_trailing_record` / `_trailing_record_to_state_entry` — already cleanly extracted.
- `core/runtime.py` getters — the `dict(...)` copy pattern is identical three times but is two lines; adding a helper would be net-negative readability.

**Commit:** `refactor(database): add _to_decimal_required helper for non-nullable conversions`.

---

## Step 7 — Exception-handling audit

Walk every `except Exception` in the codebase. For each, decide one of three roles and align the body to it:

| Role          | Body shape                                               | Examples                                             |
| ------------- | -------------------------------------------------------- | ---------------------------------------------------- |
| **Recoverable, observable** | log + return sentinel (`None` / `[]` / `pd.DataFrame()`) — caller decides what to do | `exchange.kraken.*`, `core.database.load_*`, `core.database.delete_trailing_state` |
| **Recoverable, internal**   | log + return — caller does not branch on it       | `core.logging._notify`, `services.telegram.app.notify` |
| **Fatal**     | `raise … from e` — propagate to lifespan/scheduler       | `core.database.save_*` (mutating writes), `core.scheduler.trading_session` top-level |

Apply these rules:

1. Mutating writes that fail must propagate. `core.database.save_ohlc_data` already does (`raise`); `core.database.save_closed_position` already does. Verify `core.database.save_trailing_state` does too — currently it `raise`s, which is correct. Leave them.
2. `trading.positions_manager.close_position` swallows on any exception today. This is **fatal-recoverable**: log + Telegram + return without re-raising is the right call, because the scheduler must keep ticking. Annotate the intent in a one-line comment.
3. `core.scheduler.trading_session` has no top-level `try`; APScheduler's job error handling will log and reschedule. Leave it.
4. `services.telegram.polling.*_command` handlers swallow exceptions and reply with a friendly message — already correct.
5. `core.logging._notify` and `services.telegram.app.notify` are best-effort. Already correct.

For each rule that requires a code change, comment the intent on the `except` line:

```python
except Exception as e:
    # Recoverable: scheduler must keep ticking; surface failure via Telegram.
    logging.error(f"Failed to close trailing position: {e}", to_telegram=True)
```

**Commit:** `refactor(errors): document exception-handling roles across recoverable paths`.

---

## Step 8 — TODO sweep

Two `# TODO` markers exist in code:

- `core/scheduler.py:21` — "add unit tests for trading_session, check_closed_position, …"
- `core/database.py:69` — "split this module into core/db/models.py, …"

Both are observations, not Phase-6 work. Resolution:

- Open one GitHub issue per TODO, paste the comment as the issue body, and link it back. Title pattern: `chore(scheduler): unit tests for trading_session helpers`.
- Replace each TODO comment with a one-line pointer: `# Tracked in #NN`.

If GitHub issue creation is out of band for this PR, leave the TODO comments alone and note in the PR description that they are deferred.

**Commit (optional):** `chore: replace inline TODOs with issue links`.

---

## Step 9 — CI guardrail (read-only)

Phase 7 will wire `ruff` into CI as a quality gate. Phase 6 only verifies it works locally / inside Docker. Add a one-line `Makefile` or shell script for convenience — **only if the team uses one already**; otherwise skip.

If the project does not already ship a Makefile, do not introduce one in this phase. Document the canonical invocations in `README.md` instead, under a new "Code quality" subsection:

```markdown
### Code quality

Run linting and formatting checks inside Docker:

    docker compose -f docker-compose.test.yml run --rm test ruff check .
    docker compose -f docker-compose.test.yml run --rm test ruff format --check .

Apply automatic fixes:

    docker compose -f docker-compose.test.yml run --rm test ruff check . --fix
    docker compose -f docker-compose.test.yml run --rm test ruff format .
```

**Commit:** `docs(readme): add Code quality section with ruff invocations`.

---

## Step 10 — Final verification

Inside Docker:

```
docker compose -f docker-compose.test.yml run --rm test ruff check .
docker compose -f docker-compose.test.yml run --rm test ruff format --check .
docker compose -f docker-compose.test.yml run --rm test pytest tests/unit
docker compose -f docker-compose.test.yml run --rm test pytest tests/integration   # if Kraken creds available
```

All four must pass. Coverage stays ≥ 80%.

Smoke-test the bot itself to ensure no behaviour regressed:

```
docker compose up -d
sleep 90   # let the scheduler tick at least once
curl http://localhost:8000/status   # paused: false, last_run_at: <ISO timestamp>
curl http://localhost:8000/market   # populated entries
docker compose down
```

---

## Execution order (commits)

Each bullet is one focused commit. Run `pytest tests/unit` and `ruff check .` inside Docker after each commit.

1. `chore(deps): pin ruff in requirements-dev.txt`
2. `chore(tooling): add pyproject.toml; remove pytest.ini and .coveragerc`
3. `style: apply ruff format to the entire repository`
4. `style(ruff): resolve lint diagnostics across legacy modules`
5. `feat(types): annotate core.runtime`
6. `feat(types): annotate core.scheduler`
7. `feat(types): annotate core.logging + core.validation + core.config + core.utils`
8. `feat(types): annotate exchange.kraken`
9. `feat(types): annotate trading package`
10. `feat(types): annotate services.telegram.app and finish polling`
11. `feat(types): annotate scripts/load_legacy_data`
12. `refactor(logging): normalize stdlib vs core.logging import alias`
13. `refactor(database): add _to_decimal_required helper for non-nullable conversions`
14. `refactor(kraken): collapse query/error-log boilerplate into _safe_call`
15. `refactor(errors): document exception-handling roles across recoverable paths`
16. `chore: replace inline TODOs with issue links` (optional)
17. `docs(readme): add Code quality section with ruff invocations`

---

## Acceptance checklist

Run all of these before opening the PR:

- [ ] `docker compose -f docker-compose.test.yml run --rm test ruff check .` exits `0`.
- [ ] `docker compose -f docker-compose.test.yml run --rm test ruff format --check .` exits `0`.
- [ ] `docker compose -f docker-compose.test.yml run --rm test pytest tests/unit` passes with ≥ 80% coverage.
- [ ] `docker compose -f docker-compose.test.yml run --rm test pytest tests/integration` passes when Kraken credentials are present.
- [ ] `docker compose up` starts `postgres`, `botc`, `telegram` and stays healthy for ≥ 5 minutes.
- [ ] `curl http://localhost:8000/status` returns a real `last_run_at` after the first session.
- [ ] `git diff main -- pytest.ini .coveragerc` shows both files deleted; `pyproject.toml` exists and is valid TOML (`python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"`).
- [ ] `grep -rn "logging\.logging\.getLogger" .` returns nothing.
- [ ] Every public function in `core/`, `exchange/`, `trading/`, `services/telegram/`, `api/`, `scripts/load_legacy_data.py` has both argument and return-type annotations. Spot-check by running `grep -n "^def \|^async def " <module> | grep -v "->"` per file; the only allowed misses are dunder methods on SQLAlchemy models.
- [ ] No `# TODO` markers remain in production source files (under `core/`, `exchange/`, `trading/`, `services/`, `api/`).
- [ ] `README.md` documents the `ruff` invocations.

---

## Non-goals for this phase

Explicitly out of scope — do not add any of these:

- `mypy`, `pyright`, or any other dedicated type checker. Phase 6 is annotations + ruff only; static analysis tightening is a follow-up.
- Splitting `core/database.py` per the existing TODO. Issue gets opened; the split is its own PR.
- Adding the `N` (PEP 8 naming) rule set — it would force renames like `infoLog` → `info_log` that ripple through call sites.
- New features, new endpoints, new schema columns, new tests beyond what is needed to keep coverage at 80%.
- Touching `services/telegram/client.py` headers — already minimal.
- CI workflow changes (Phase 7).
- `CHANGELOG.md` entry (Phase 8).
- Replacing `dict[str, Any]` annotations with `TypedDict` — defer until the runtime/state modules are restructured.
