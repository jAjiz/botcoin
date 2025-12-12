import os
import logging
from dotenv import load_dotenv

load_dotenv()

# KRAKEN API Credentials
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET")

# Telegram Bot Credentials
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID"))
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", 20))

# Bot Settings
MODE = os.getenv("MODE", "rebuy")  # Options: "rebuy", "multipliers"
SLEEPING_INTERVAL = int(os.getenv("SLEEPING_INTERVAL", 60))  # 1 minute
ATR_DATA_DAYS = int(os.getenv("ATR_DATA_DAYS", 60))

# Pairs names map and info
PAIRS = {pair: {} for pair in os.getenv("PAIRS", "XBTEUR").split(",")}

# Trading params
DFLT_K_ACT = float(os.getenv("K_ACT", 4.5))
DFLT_K_STOP_SELL = float(os.getenv("K_STOP_SELL", 2.5))
DFLT_K_STOP_BUY = float(os.getenv("K_STOP_BUY", 2.5))
DFLT_MIN_MARGIN_PCT = float(os.getenv("MIN_MARGIN", 0.01))

def _build_trading_params():
    params = {}
    for pair in PAIRS.keys():
        k_act = float(os.getenv(f"K_ACT_{pair}", DFLT_K_ACT))
        k_stop_sell = float(os.getenv(f"K_STOP_SELL_{pair}", DFLT_K_STOP_SELL))
        k_stop_buy = float(os.getenv(f"K_STOP_BUY_{pair}", DFLT_K_STOP_BUY))
        k_stop = (k_stop_sell + k_stop_buy) / 2
        min_margin_pct = float(os.getenv(f"MIN_MARGIN_{pair}", DFLT_MIN_MARGIN_PCT))
        atr_min_pct = min_margin_pct / (k_act - k_stop)

        params[pair] = {
            "K_ACT": k_act,
            "K_STOP_SELL": k_stop_sell,
            "K_STOP_BUY": k_stop_buy,
            "K_STOP": k_stop,
            "MIN_MARGIN_PCT": min_margin_pct,
            "ATR_MIN_PCT": atr_min_pct,
        }
    return params

TRADING_PARAMS = _build_trading_params()

# Asset minimum allocation
def _build_asset_min_allocation():
    allocations = {}
    for pair in PAIRS.keys():
        allocations[pair] = float(os.getenv(f"MIN_ALLOCATION_{pair}", 0))
    return allocations

ASSET_MIN_ALLOCATION = _build_asset_min_allocation()