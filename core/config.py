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

# Bot Settings
SLEEPING_INTERVAL = int(os.getenv("SLEEPING_INTERVAL", 60))  # 1 minute
PARAM_SESSIONS = int(os.getenv("PARAM_SESSIONS", 720))  # 720 sessions (1min between) = 12 hours
CANDLE_TIMEFRAME = int(os.getenv("CANDLE_TIMEFRAME", 15))  # Candle timeframe in minutes
MARKET_DATA_DAYS = int(os.getenv("MARKET_DATA_DAYS", 60))  # 60 days
ATR_PERIOD = int(os.getenv("ATR_PERIOD", 14))  # ATR calculation period in candles
ATR_DESV_LIMIT = float(os.getenv("ATR_DESV_LIMIT", 0.2))  # ATR recalibration limit (20%)
MIN_VALUE = float(os.getenv("MIN_VALUE", 10))  # Minimum value operation in fiat

# Pairs names map and info
PAIRS = {pair: {} for pair in os.getenv("PAIRS", "").split(",")}


# Trading params
def _build_trading_params() -> dict[str, dict[str, dict[str, Any]]]:
    params = {}
    for pair in PAIRS:
        params[pair] = {
            "sell": {
                "K_ACT": os.getenv(f"{pair}_SELL_K_ACT", os.getenv(f"{pair}_K_ACT", None)),
                "MIN_MARGIN": os.getenv(f"{pair}_SELL_MIN_MARGIN", os.getenv(f"{pair}_MIN_MARGIN", 0)),
            },
            "buy": {
                "K_ACT": os.getenv(f"{pair}_BUY_K_ACT", os.getenv(f"{pair}_K_ACT", None)),
                "MIN_MARGIN": os.getenv(f"{pair}_BUY_MIN_MARGIN", os.getenv(f"{pair}_MIN_MARGIN", 0)),
            },
        }
    return params


TRADING_PARAMS = _build_trading_params()


# Asset allocation
def _build_asset_allocation() -> dict[str, dict[str, Any]]:
    allocations = {}
    for pair in PAIRS:
        allocations[pair] = {
            "TARGET_PCT": os.getenv(f"{pair}_TARGET_PCT", 0),
            "HODL_PCT": os.getenv(f"{pair}_HODL_PCT", 0),
        }
    return allocations


ASSET_ALLOCATION = _build_asset_allocation()

# Market analyzer settings
MARKET_ANALYZER = {
    "DEFAULT_ORDER": 20,
    "MINIMUM_CHANGE_PCT": float(os.getenv("MINIMUM_CHANGE_PCT", 0.02)),  # Default 2%
}


# K_STOP percentiles
def _build_percentiles() -> dict[str, dict[str, float]]:
    percentiles = {}
    for pair in PAIRS:
        percentiles[pair] = {
            "LL": float(os.getenv(f"{pair}_STOP_PCT_LL", 0.90)),
            "LV": float(os.getenv(f"{pair}_STOP_PCT_LV", 0.90)),
            "MV": float(os.getenv(f"{pair}_STOP_PCT_MV", 0.90)),
            "HV": float(os.getenv(f"{pair}_STOP_PCT_HV", 0.90)),
            "HH": float(os.getenv(f"{pair}_STOP_PCT_HH", 0.90)),
        }
    return percentiles


STOP_PERCENTILES = _build_percentiles()

# CONSTANTS DEFINITION
FIAT_CODE = "ZEUR"
VOLATILITY_LEVELS = ("LL", "LV", "MV", "HV", "HH")
