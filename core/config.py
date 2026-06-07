import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# KRAKEN API Credentials
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET")

# Telegram Bot Credentials
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_USER_ID = os.getenv("TELEGRAM_USER_ID")
TELEGRAM_POLL_INTERVAL = int(os.getenv("TELEGRAM_POLL_INTERVAL", 0))  # in seconds

# Database settings
POSTGRES_DB = os.getenv("POSTGRES_DB", "DBbotc")
POSTGRES_USER = os.getenv("POSTGRES_USER", "botc")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")

# API settings
API_SECRET_TOKEN = os.getenv("API_SECRET_TOKEN")
# Explicit opt-in to run without API auth. Use with caution.
ALLOW_NO_AUTH = os.getenv("ALLOW_NO_AUTH", "false").lower() == "true"

# Kill switch for the optimizer endpoints. The optimizer is CPU- and RAM-bound
# and, on a resource-constrained host (e.g. a free-tier micro VM), can starve
# the trading engine and lock up the machine. Set to true there to reject new
# optimizer jobs with 503 while leaving the rest of the API running.
OPTIMIZER_DISABLED = os.getenv("OPTIMIZER_DISABLED", "false").lower() == "true"

# Bot Settings
SLEEPING_INTERVAL = int(os.getenv("SLEEPING_INTERVAL", 60))  # 1 minute
PARAM_SESSIONS = int(os.getenv("PARAM_SESSIONS", 720))  # 720 sessions (1min between) = 12 hours
CANDLE_TIMEFRAME = int(os.getenv("CANDLE_TIMEFRAME", 15))  # Candle timeframe in minutes
ATR_PERIOD = int(os.getenv("ATR_PERIOD", 14))  # ATR calculation period in candles
ATR_DESV_LIMIT = float(os.getenv("ATR_DESV_LIMIT", 0.2))  # ATR recalibration limit (20%)
MIN_VALUE = float(os.getenv("MIN_VALUE", 10))  # Minimum value operation in fiat

# Master switch for trading. When false the scheduler still ingests OHLC,
# calibrates, updates the runtime cache, records sessions and serves the API and
# optimizer — but never opens, manages or closes positions (no Kraken order
# placement). Intended for a non-trading replica, e.g. a local stack used to run
# the optimizer with full features. Always true in production.
TRADING_ENABLED = os.getenv("TRADING_ENABLED", "true").lower() == "true"

# Pairs names map and info
PAIRS = {pair: {} for pair in os.getenv("PAIRS", "").split(",")}

# CONSTANTS DEFINITION
FIAT_CODE = "ZEUR"
VOLATILITY_LEVELS = ("LL", "LV", "MV", "HV", "HH")
STOP_PCT_DEFAULT = 0.90  # Fallback STOP_PCT if not set for a level


# Trading params
def _build_trading_params() -> dict[str, dict[str, dict[str, Any]]]:
    params = {}
    for pair in PAIRS:
        params[pair] = {
            "sell": {
                "K_ACT": os.getenv(f"{pair}_SELL_K_ACT", os.getenv(f"{pair}_K_ACT")),
                "MIN_MARGIN": os.getenv(f"{pair}_SELL_MIN_MARGIN", os.getenv(f"{pair}_MIN_MARGIN")),
            },
            "buy": {
                "K_ACT": os.getenv(f"{pair}_BUY_K_ACT", os.getenv(f"{pair}_K_ACT")),
                "MIN_MARGIN": os.getenv(f"{pair}_BUY_MIN_MARGIN", os.getenv(f"{pair}_MIN_MARGIN")),
            },
        }
    return params


TRADING_PARAMS = _build_trading_params()


# Asset allocation
def _build_asset_allocation() -> dict[str, dict[str, Any]]:
    allocations = {}
    for pair in PAIRS:
        allocations[pair] = {
            "TARGET_PCT": os.getenv(f"{pair}_TARGET_PCT"),
            "HODL_PCT": os.getenv(f"{pair}_HODL_PCT"),
        }
    return allocations


ASSET_ALLOCATION = _build_asset_allocation()

# Market analyzer settings
MARKET_ANALYZER = {
    "DEFAULT_ORDER": 20,
    "MINIMUM_CHANGE_PCT": float(os.getenv("MINIMUM_CHANGE_PCT", 0.02)),  # Default 2%
}


# K_STOP percentiles
def _build_percentiles() -> dict[str, dict[str, Any]]:
    percentiles = {}
    for pair in PAIRS:
        percentiles[pair] = {level: os.getenv(f"{pair}_STOP_PCT_{level}") for level in VOLATILITY_LEVELS}
    return percentiles


STOP_PERCENTILES = _build_percentiles()
