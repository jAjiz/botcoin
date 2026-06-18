# Configuration Reference

All configuration is loaded from `.env` at startup. Copy `.env.example` to `.env` and fill in the required values before running `docker compose up`.

---

## Service URLs

Set automatically by Docker Compose via `docker-compose.yml`. Override only when running services outside of Docker.

| Variable | Default | Effect |
|---|---|---|
| `API_BASE_URL` | `http://localhost:8000` | Base URL the `telegram` service uses to reach the `botc` API (Docker Compose sets `http://botc:8000`) |
| `TELEGRAM_SERVICE_URL` | `http://telegram:8001` | URL `core/logging.py` posts `to_telegram=True` messages to |

---

## Kraken API

| Variable | Required | Default | Effect |
|---|---|---|---|
| `KRAKEN_API_KEY` | yes | — | API key from your Kraken account (read only + trade permissions) |
| `KRAKEN_API_SECRET` | yes | — | Matching API secret; never commit `.env` to version control |

---

## Telegram

| Variable | Required | Default | Effect |
|---|---|---|---|
| `TELEGRAM_TOKEN` | yes | — | Bot token from @BotFather |
| `TELEGRAM_USER_ID` | yes | — | Your numeric Telegram user ID; commands from any other user are silently ignored |
| `TELEGRAM_ENABLED` | no | `true` | Set to `false` to start the stack without Telegram (useful in dev; `telegram` service still starts but sends no messages) |
| `TELEGRAM_POLL_INTERVAL` | no | `0` | Seconds between PTB long-poll requests (`.env.example` ships `10`) |

---

## API authentication

| Variable | Required | Default | Effect |
|---|---|---|---|
| `API_SECRET_TOKEN` | yes* | — | Bearer token protecting all `botc` REST endpoints and the `telegram` `/notify` webhook. Both services read this from the same `.env`. If unset, the app refuses to start unless `ALLOW_NO_AUTH=true` is also set |
| `ALLOW_NO_AUTH` | no | `false` | Set to `true` to start without authentication (development only; never use in production) |
| `MAX_CONCURRENT_JOBS` | no | `1` | Maximum number of concurrent optimizer jobs. `0` disables the optimizer entirely (`POST /optimizer/jobs` returns `503`); `≥1` allows up to N jobs in flight (`409` when all slots are busy). On a resource-constrained host (e.g. a free-tier micro VM) set to `0` to prevent the CPU-bound search from starving the trading engine. Each `AUTO` job spawns ~2 extra worker processes. |

---

## Bot behaviour

| Variable | Required | Default | Effect |
|---|---|---|---|
| `PAIRS` | yes | — | Comma-separated list of Kraken pair identifiers, e.g. `XBTEUR,ETHEUR` |
| `TRADING_ENABLED` | no | `true` | Master trading switch. When `false`, the scheduler still ingests OHLC, calibrates, updates the runtime cache, records sessions and serves the API/optimizer, but never opens, manages or closes positions (no Kraken order placement). Use for a non-trading replica (e.g. a local stack running the optimizer). Must stay `true` in production; do not enable on an instance holding open positions (their trailing stop would freeze) |
| `SLEEPING_INTERVAL` | no | `60` | Seconds between trading sessions |
| `PARAM_SESSIONS` | no | `720` | Sessions before recalculating K_STOP parameters (~12 h at 60 s intervals) |
| `CANDLE_TIMEFRAME` | no | `15` | OHLC candle size in minutes |
| `ATR_PERIOD` | no | `14` | Number of candles in the ATR rolling window |
| `ATR_DESV_LIMIT` | no | `0.2` | Fractional ATR drift that triggers position recalibration (0.2 = 20 %) |
| `MIN_VALUE` | no | `10` | Minimum operation value in EUR; positions below this threshold are skipped |
| `MINIMUM_CHANGE_PCT` | no | `0.02` | Minimum relative price change for a local extremum to count as a pivot (2 %) |

---

## Per-pair parameters

For each pair listed in `PAIRS`, define the following variables by replacing `PAIR` with the pair identifier (e.g. `XBTEUR`).

| Pattern | Required | Default | Effect |
|---|---|---|---|
| `PAIR_TARGET_PCT` | yes | — | Target portfolio allocation for this asset as a percentage of total portfolio value |
| `PAIR_HODL_PCT` | yes | — | Minimum hold threshold; the bot does not sell below this percentage |
| `PAIR_K_ACT` | no | — | ATR multiplier for activation price distance (single per pair — no per-side variants). If omitted, `K_STOP × ATR + MIN_MARGIN × entry_price` is used instead |
| `PAIR_MIN_MARGIN` | no | — | Minimum profit margin from entry price as a fraction (e.g. `0.009` = 0.9 %; single per pair — no per-side variants). Used only when `K_ACT` is not set |
| `PAIR_STOP_PCT_LL` | yes | — | K_STOP percentile for Very Low Volatility (LL) regime |
| `PAIR_STOP_PCT_LV` | yes | — | K_STOP percentile for Low Volatility (LV) regime |
| `PAIR_STOP_PCT_MV` | yes | — | K_STOP percentile for Medium Volatility (MV) regime |
| `PAIR_STOP_PCT_HV` | yes | — | K_STOP percentile for High Volatility (HV) regime |
| `PAIR_STOP_PCT_HH` | yes | — | K_STOP percentile for Very High Volatility (HH) regime |

See [trading-strategy.md](trading-strategy.md) for how K_STOP percentiles are derived and what values to choose.

### DB-authoritative config (Phase 1)

The parameters above (`TARGET_PCT`, `HODL_PCT`, `K_ACT`, `MIN_MARGIN`, `STOP_PCT_<level>`) are **DB-authoritative** once the bot has booted. On first boot `core/config_store.py` seeds the `pair_config` table from `.env`; subsequent boots read from the DB and `.env` values for these fields are ignored.

To query or modify them at runtime without a restart:

- `GET /config` — returns current config for all pairs
- `GET /config/{pair}` — returns config for one pair
- `PATCH /config/{pair}` — updates one or more fields for a pair (validated, persisted, applied immediately; `stop_pct` changes trigger `K_STOP` recalculation on the next scheduler session)

Telegram equivalents: `/config [pair]` (read) and `/setconfig <pair> <field> <value>` (write).

All REST endpoints require the `X-Api-Token` header (see [API authentication](#api-authentication)).

---

## PostgreSQL

| Variable | Required | Default | Effect |
|---|---|---|---|
| `POSTGRES_DB` | no | `DBbotc` | Database name |
| `POSTGRES_USER` | no | `botc` | Application user (read/write) |
| `POSTGRES_PASSWORD` | yes | — | Password for `POSTGRES_USER` |
| `POSTGRES_HOST` | no | `postgres` | Hostname (Docker internal DNS; override for external Postgres) |
| `POSTGRES_PORT` | no | `5432` | Port |

---

## Grafana

| Variable | Required | Default | Effect |
|---|---|---|---|
| `GRAFANA_DB_PASSWORD` | yes | — | Password for the `grafana_reader` Postgres role; set during `alembic upgrade head` (migration `20260512_01`) |
| `GF_SECURITY_ADMIN_USER` | no | `admin` | Grafana admin username; read by Grafana on first boot only |
| `GF_SECURITY_ADMIN_PASSWORD` | yes | — | Grafana admin password; stored as bcrypt in the `gf_data` volume after first boot |
