import os
from dotenv import load_dotenv

load_dotenv()

# KRAKEN API Credentials
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET")

# Telegram Bot Credentials
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID"))

# Bot Settings
MODE = os.getenv("MODE", "multipliers")  # Options: "multipliers", "rebuy"
SLEEPING_INTERVAL = int(os.getenv("SLEEPING_INTERVAL", 60))  # 1 minute

# Multiplier mode Settings
MULT_K_ACT = float(os.getenv("MULT_K_ACT", 4.5))
MULT_K_STOP = float(os.getenv("MULT_K_STOP", 2.5))
MIN_MARGIN_PCT = float(os.getenv("MIN_MARGIN_PCT", 0.01))  # 1%
ATR_PCT_MIN = MIN_MARGIN_PCT / (MULT_K_ACT - MULT_K_STOP)
MIN_BTC_ALLOCATION_PCT = float(os.getenv("MIN_BTC_ALLOCATION_PCT", 0.60))  # 60%

# Rebuy mode Settings
REBUY_K_STOP = float(os.getenv("REBUY_K_STOP", 3))