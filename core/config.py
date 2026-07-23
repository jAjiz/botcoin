import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# Every variable below is documented in docs/configuration.md (defaults, units,
# effects) and annotated in .env.example. Keep those in sync when adding one.

KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET")

TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_USER_ID = os.getenv("TELEGRAM_USER_ID")
TELEGRAM_POLL_INTERVAL = int(os.getenv("TELEGRAM_POLL_INTERVAL", 0))

POSTGRES_DB = os.getenv("POSTGRES_DB", "DBbotc")
POSTGRES_USER = os.getenv("POSTGRES_USER", "botc")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")

API_SECRET_TOKEN = os.getenv("API_SECRET_TOKEN")
ALLOW_NO_AUTH = os.getenv("ALLOW_NO_AUTH", "false").lower() == "true"

MAX_CONCURRENT_JOBS = max(0, int(os.getenv("MAX_CONCURRENT_JOBS", "1")))
SESSION_FAILURE_ALERT_THRESHOLD = max(1, int(os.getenv("SESSION_FAILURE_ALERT_THRESHOLD", "3")))

SLEEPING_INTERVAL = int(os.getenv("SLEEPING_INTERVAL", 60))
PARAM_SESSIONS = int(os.getenv("PARAM_SESSIONS", 720))
CANDLE_TIMEFRAME = int(os.getenv("CANDLE_TIMEFRAME", 15))
ATR_PERIOD = int(os.getenv("ATR_PERIOD", 14))
ATR_DESV_LIMIT = float(os.getenv("ATR_DESV_LIMIT", 0.2))
MIN_VALUE = float(os.getenv("MIN_VALUE", 10))

TRADING_ENABLED = os.getenv("TRADING_ENABLED", "true").lower() == "true"

PAIRS = {pair: {} for pair in os.getenv("PAIRS", "").split(",")}

FIAT_CODE = "ZEUR"
VOLATILITY_LEVELS = ("LL", "LV", "MV", "HV", "HH")
STOP_PCT_DEFAULT = 0.90  # fallback when a level has no configured percentile


def _build_trading_params() -> dict[str, dict[str, Any]]:
    params = {}
    for pair in PAIRS:
        params[pair] = {
            "K_ACT": os.getenv(f"{pair}_K_ACT"),
            "MIN_MARGIN": os.getenv(f"{pair}_MIN_MARGIN"),
            "K_STOP": {"buy": {}, "sell": {}},
        }
    return params


TRADING_PARAMS = _build_trading_params()


def _build_asset_allocation() -> dict[str, dict[str, Any]]:
    allocations = {}
    for pair in PAIRS:
        allocations[pair] = {
            "TARGET_PCT": os.getenv(f"{pair}_TARGET_PCT"),
            "HODL_PCT": os.getenv(f"{pair}_HODL_PCT"),
        }
    return allocations


ASSET_ALLOCATION = _build_asset_allocation()

MARKET_ANALYZER = {
    "DEFAULT_ORDER": 20,
    "MINIMUM_CHANGE_PCT": float(os.getenv("MINIMUM_CHANGE_PCT", 0.02)),
}


def _build_percentiles() -> dict[str, dict[str, Any]]:
    percentiles = {}
    for pair in PAIRS:
        percentiles[pair] = {level: os.getenv(f"{pair}_STOP_PCT_{level}") for level in VOLATILITY_LEVELS}
    return percentiles


STOP_PERCENTILES = _build_percentiles()


# The "flat" dict (target_pct, hodl_pct, k_act, min_margin, stop_pct_ll..hh) is the
# representation shared by validation, the config store and the API.
def set_pair_config(pair: str, typed: dict[str, Any]) -> None:
    TRADING_PARAMS[pair]["K_ACT"] = typed["k_act"]
    TRADING_PARAMS[pair]["MIN_MARGIN"] = typed["min_margin"]
    ASSET_ALLOCATION[pair]["TARGET_PCT"] = typed["target_pct"]
    ASSET_ALLOCATION[pair]["HODL_PCT"] = typed["hodl_pct"]
    for lvl in VOLATILITY_LEVELS:
        STOP_PERCENTILES[pair][lvl] = typed[f"stop_pct_{lvl.lower()}"]


def get_pair_config(pair: str) -> dict[str, Any]:
    flat = {
        "k_act": TRADING_PARAMS[pair]["K_ACT"],
        "min_margin": TRADING_PARAMS[pair]["MIN_MARGIN"],
        "target_pct": ASSET_ALLOCATION[pair]["TARGET_PCT"],
        "hodl_pct": ASSET_ALLOCATION[pair]["HODL_PCT"],
    }
    for lvl in VOLATILITY_LEVELS:
        flat[f"stop_pct_{lvl.lower()}"] = STOP_PERCENTILES[pair][lvl]
    return flat
