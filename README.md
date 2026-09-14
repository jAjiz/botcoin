# BoTCoin — Autonomous Trading Bot Backend

> **Status: closed — September 2026.** The bot no longer runs and the production
> stack is decommissioned. Twenty-four strategy avenues were measured and none
> beat holding the base asset out of sample, so live trading was stopped. The
> repository is kept as it was on the last day it ran, because the engineering is
> the point and **the negative result is part of it** — see [The result](#the-result).

[![CI](https://github.com/jAjiz/BoTCoin/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/jAjiz/BoTCoin/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-%E2%89%A580%25-brightgreen.svg)](https://github.com/jAjiz/BoTCoin/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)

BoTCoin is a production-grade backend service built using modern Python engineering practices. It ran an ATR-based trailing-stop strategy against Kraken's EUR pairs, persisted all state in PostgreSQL, exposed a REST control surface via FastAPI, shipped a Grafana observability layer, and was operated through a Telegram bot controller. The whole stack still starts with a single `docker compose up`, against your own credentials.

<table>
  <tr>
    <td><img src="docs/images/grafana.png" alt="Grafana dashboard — market, ATR, position, and performance panels"></td>
    <td><img src="docs/images/telegram.png" alt="Telegram bot — market and position commands"></td>
  </tr>
</table>

---

## Architecture

```mermaid
graph BT
    subgraph stack["Docker Compose stack"]
        direction TB
        botc["botc :8000\nFastAPI + APScheduler\nTrading engine"]
        telegram["telegram :8001\nFastAPI + PTB polling\nTelegram interface"]
        postgres[("postgres :5432\nPostgreSQL 16\nAll state + history")]
        grafana["grafana :3000\nGrafana 11\nObservability dashboard"]
    end

    botc -->|"SQLAlchemy"| postgres
    grafana -->|"grafana_reader"| postgres
    telegram -->|"commands"| botc
    botc -->|"notifications"| telegram
```

Two application containers share one network. `botc` is the sole writer to every table. `telegram` is a thin API client — it reads and controls the bot exclusively through `botc`'s REST endpoints. Grafana reads the same database through a least-privilege `grafana_reader` role created by an Alembic migration.

---

## Quick start

```bash
cp .env.example .env   # fill in required values — see docs/configuration.md
docker compose up -d --build
```

| Service | URL |
|---|---|
| Trading API (Swagger UI) | http://localhost:8000/docs |
| Grafana dashboard | http://localhost:3000 |

```bash
docker compose logs -f botc        # watch trading sessions
docker compose down                # stop all services
```

---

## Project status

BoTCoin reached its goal of a production-grade backend service, and that milestone is closed — see the archived [V2 roadmap](docs/v2/ROADMAP.md) for the full delivered scope.

The strategy line is closed too, and with it the project. The backlog at [docs/BACKLOG.md](docs/BACKLOG.md) records what shipped, and the one card that closed it. Nothing is planned.

---

## Documentation

| Document | Contents |
|---|---|
| [docs/configuration.md](docs/configuration.md) | Every `.env` variable, its default, and its effect |
| [docs/trading-strategy.md](docs/trading-strategy.md) | ATR classification, K_STOP calibration, position lifecycle |
| [docs/operations.md](docs/operations.md) | Local dev, production deploy, rollback, monitoring, troubleshooting |
| [docs/specs/optimizer-validation-design.md](docs/specs/optimizer-validation-design.md) | **The study** — 24 avenues, the measurement traps, and the surviving tools |
| [docs/BACKLOG.md](docs/BACKLOG.md) | Feature backlog — what shipped, and the card that closed the project |
| [docs/v2/ROADMAP.md](docs/v2/ROADMAP.md) | Archived V2 roadmap (closed) and phase plans |
| [CLAUDE.md](CLAUDE.md) | Architecture, conventions, and the **Design choices** list |

---

## The result

Most trading bots in a portfolio show a backtest and claim an edge. This one asked
whether the edge exists, measured it, and published the answer.

The question was not "is it profitable". In a rising market anything long is
profitable. The question was whether the bot **accumulates more of the base asset
than holding it**, so holding scores 0 % by construction and is the bar every
figure is measured against. The answer is **no**, for every parameterisation, gate
and objective tested.

- **In-sample selection has no forward value.** Median percentile 50 of the
  forward distribution, over nine decision dates enumerating the whole
  105-configuration space.
- **The market period decides, not the parameters.** In one 60-day period 0 of 105
  configurations beat holding; in the next, 100 of 105 do.
- **Over three continuous years it loses 78 % of the base asset in four
  operations.** 2023-2025, while holding returned +384 % in euros. 0 of 105 beat
  holding.
- **No gate fixes it, including one fitted with hindsight.** Hill-climbing 366 free
  per-day booleans against the bot's own 2024 result reaches +113.1 %. The same
  climb on a **shuffled, memoryless** year reaches +173.5 %. In four cells of four,
  noise admits a better oracle gate than the real market does.

The full study — 24 avenues, the decisions taken, the measurement traps and the
surviving tools — is in
[`docs/specs/optimizer-validation-design.md`](docs/specs/optimizer-validation-design.md).
Read "Measurement traps" first if you read nothing else.

---

*Cryptocurrency trading involves substantial financial risk. This software is not financial advice. Use at your own risk.*
