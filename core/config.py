import os
from dotenv import load_dotenv

load_dotenv()

# KRAKEN API Credentials
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET")

# Telegram Bot Credentials
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_USER_ID = os.getenv("TELEGRAM_USER_ID")
TELEGRAM_POLL_INTERVAL = int(os.getenv("TELEGRAM_POLL_INTERVAL", 0)) # in seconds

# Bot Settings
SLEEPING_INTERVAL = int(os.getenv("SLEEPING_INTERVAL", 60))  # 1 minute
PARAM_SESSIONS = int(os.getenv("PARAM_SESSIONS", 720)) # 720 sessions (1min between) = 12 hours
CANDLE_TIMEFRAME = int(os.getenv("CANDLE_TIMEFRAME", 15))  # Candle timeframe in minutes
MARKET_DATA_DAYS = int(os.getenv("MARKET_DATA_DAYS", 60)) # 60 days
ATR_PERIOD = int(os.getenv("ATR_PERIOD", 14))  # ATR calculation period in candles
ATR_DESV_LIMIT = float(os.getenv("ATR_DESV_LIMIT", 0.2))  # ATR recalibration limit (20%)
FIAT_CODE = os.getenv("FIAT_CODE", "ZEUR")  # Fiat currency code
MIN_VALUE = float(os.getenv("MIN_VALUE", 10))  # Minimum value operation in fiat

# Pairs names map and info
PAIRS = {pair: {} for pair in os.getenv("PAIRS", "").split(",")}

# Trading params
def _build_trading_params():
    params = {}
    for pair in PAIRS.keys():
        params[pair] = {
            "sell": {
                "K_STOP": {"LV": None, "MV": None, "HV": None, "EV": None},
                "K_ACT": os.getenv(f"{pair}_SELL_K_ACT", None),
                "MIN_MARGIN": os.getenv(f"{pair}_SELL_MIN_MARGIN", 0)
            },
            "buy": {
                "K_STOP": {"LV": None, "MV": None, "HV": None, "EV": None},
                "K_ACT": os.getenv(f"{pair}_BUY_K_ACT", None),
                "MIN_MARGIN": os.getenv(f"{pair}_BUY_MIN_MARGIN", 0)
            }
        }
    return params

TRADING_PARAMS = _build_trading_params()

# Asset allocation
def _build_asset_allocation():
    allocations = {}
    for pair in PAIRS.keys():
        allocations[pair] = {
            "TARGET_PCT": os.getenv(f"{pair}_TARGET_PCT", 0),
            "HODL_PCT": os.getenv(f"{pair}_HODL_PCT", 0)
        }
    return allocations

ASSET_ALLOCATION = _build_asset_allocation()

# Market analyzer settings
MARKET_ANALYZER = {
    "DEFAULT_ORDER": 20,
    "MINIMUM_CHANGE_PCT": float(os.getenv("MINIMUM_CHANGE_PCT", 0.02))  # Default 2%
}