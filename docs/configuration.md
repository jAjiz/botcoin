# Configuration Reference

All configuration is loaded from `.env` at startup. Copy `.env.example` to `.env` and fill in the required values before running `docker compose up`.

---

## Service URLs

Set automatically by Docker Compose via `docker-compose.yml`. Override only when running services outside of Docker.

| Variable | Default | Effect |
|---|---|---|
| `API_BASE_URL` | `http://botc:8000` | Base URL the `telegram` service uses to reach the `botc` API |
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
| `TELEGRAM_POLL_INTERVAL` | no | `10` | Seconds between PTB long-poll requests |

---

## API authentication

| Variable | Required | Default | Effect |
|---|---|---|---|
| `API_SECRET_TOKEN` | yes* | — | Bearer token protecting all `botc` REST endpoints and the `telegram` `/notify` webhook. Both services read this from the same `.env`. If unset, the app refuses to start unless `ALLOW_NO_AUTH=true` is also set |
| `ALLOW_NO_AUTH` | no | `false` | Set to `true` to start without authentication (development only; never use in production) |

---

## Bot behaviour

| Variable | Required | Default | Effect |
|---|---|---|---|
| `PAIRS` | yes | — | Comma-separated list of Kraken pair identifiers, e.g. `XBTEUR,ETHEUR` |
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
| `PAIR_K_ACT` | no | — | ATR multiplier for activation price distance. If omitted, `K_STOP × ATR + MIN_MARGIN × entry_price` is used instead |
| `PAIR_SELL_K_ACT` / `PAIR_BUY_K_ACT` | no | — | Per-side overrides for `K_ACT` |
| `PAIR_MIN_MARGIN` | no | — | Minimum profit margin from entry price as a fraction (e.g. `0.009` = 0.9 %). Used only when `K_ACT` is not set |
| `PAIR_SELL_MIN_MARGIN` / `PAIR_BUY_MIN_MARGIN` | no | — | Per-side overrides for `MIN_MARGIN` |
| `PAIR_STOP_PCT_LL` | yes | — | K_STOP percentile for Very Low Volatility (LL) regime |
| `PAIR_STOP_PCT_LV` | yes | — | K_STOP percentile for Low Volatility (LV) regime |
| `PAIR_STOP_PCT_MV` | yes | — | K_STOP percentile for Medium Volatility (MV) regime |
| `PAIR_STOP_PCT_HV` | yes | — | K_STOP percentile for High Volatility (HV) regime |
| `PAIR_STOP_PCT_HH` | yes | — | K_STOP percentile for Very High Volatility (HH) regime |

See [trading-strategy.md](trading-strategy.md) for how K_STOP percentiles are derived and what values to choose.

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
