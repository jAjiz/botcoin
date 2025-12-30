import os
from dotenv import load_dotenv

load_dotenv()

# KRAKEN API Credentials
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET")

# Telegram Bot Credentials
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", 20))

# Bot Settings
MODE = os.getenv("MODE")  # Options: "onek", "dualk"
SLEEPING_INTERVAL = int(os.getenv("SLEEPING_INTERVAL", 60))  # 1 minute
ATR_DATA_DAYS = int(os.getenv("ATR_DATA_DAYS", 60)) # 60 days
ATR_INTERVAL = int(os.getenv("ATR_INTERVAL", 15))  # ATR chart timeframe in minutes
ATR_PERIOD = int(os.getenv("ATR_PERIOD", 14))  # ATR calculation period in candles

# Pairs names map and info
PAIRS = {pair: {} for pair in os.getenv("PAIRS", "").split(",")}

# Trading params (using -1 as default to detect missing env vars)
SELL_K_ACT = float(os.getenv("SELL_K_ACT", -1))
SELL_K_STOP = float(os.getenv("SELL_K_STOP", -1))
SELL_MIN_MARGIN = float(os.getenv("SELL_MIN_MARGIN", -1))

BUY_K_ACT = float(os.getenv("BUY_K_ACT", -1))
BUY_K_STOP = float(os.getenv("BUY_K_STOP", -1))
BUY_MIN_MARGIN = float(os.getenv("BUY_MIN_MARGIN", -1))

def _build_trading_params():
    params = {}
    for pair in PAIRS.keys():
        if MODE == "onek":
            # OneK mode: only needs K_STOP and MIN_MARGIN
            params[pair] = {
                "sell": {
                    "K_STOP": float(os.getenv(f"{pair}_SELL_K_STOP", SELL_K_STOP)),
                    "MIN_MARGIN": float(os.getenv(f"{pair}_SELL_MIN_MARGIN", SELL_MIN_MARGIN))
                },
                "buy": {
                    "K_STOP": float(os.getenv(f"{pair}_BUY_K_STOP", BUY_K_STOP)),
                    "MIN_MARGIN": float(os.getenv(f"{pair}_BUY_MIN_MARGIN", BUY_MIN_MARGIN))
                }
            }
        else:  # MODE == "dualk"
            # DualK mode: needs K_ACT, K_STOP, MIN_MARGIN, and ATR_MIN 
            params[pair] = {
                "sell": {
                    "K_ACT": float(os.getenv(f"{pair}_SELL_K_ACT", SELL_K_ACT)),
                    "K_STOP": float(os.getenv(f"{pair}_SELL_K_STOP", SELL_K_STOP)),
                    "MIN_MARGIN": float(os.getenv(f"{pair}_SELL_MIN_MARGIN", SELL_MIN_MARGIN)),
                    "ATR_MIN": None  # Calculated in validation
                },
                "buy": {
                    "K_ACT": float(os.getenv(f"{pair}_BUY_K_ACT", BUY_K_ACT)),
                    "K_STOP": float(os.getenv(f"{pair}_BUY_K_STOP", BUY_K_STOP)),
                    "MIN_MARGIN": float(os.getenv(f"{pair}_BUY_MIN_MARGIN", BUY_MIN_MARGIN)),
                    "ATR_MIN": None  # Calculated in validation
                }
            }

    return params

TRADING_PARAMS = _build_trading_params()

# Asset minimum allocation
def _build_asset_min_allocation():
    allocations = {}
    for pair in PAIRS.keys():
        allocations[pair] = float(os.getenv(f"{pair}_MIN_ALLOCATION", 0))
    return allocations

ASSET_MIN_ALLOCATION = _build_asset_min_allocation()

# Recenter settings
def _build_recenter_params():
    params = {}
    for pair in PAIRS.keys():
        params[pair] = {
            "ATR_MULT": float(os.getenv(f"{pair}_RECENTER_ATR_MULT", 0)),
            "PRICE_PCT": float(os.getenv(f"{pair}_RECENTER_PRICE_PCT", 0))
        }
    return params

RECENTER_PARAMS = _build_recenter_params()
