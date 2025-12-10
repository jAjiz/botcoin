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

# Trading Settings
K_ACT = float(os.getenv("K_ACT", 4.5))

K_STOP_SELL = float(os.getenv("K_STOP_SELL", 2.6))
K_STOP_BUY = float(os.getenv("K_STOP_BUY", 3.6))
K_STOP = (K_STOP_SELL + K_STOP_BUY) / 2

MIN_MARGIN_PCT = float(os.getenv("MIN_MARGIN", 0.01))  # 1%
ATR_MIN_PCT = MIN_MARGIN_PCT / (K_ACT - K_STOP)

MIN_BTC_PCT = float(os.getenv("MIN_BTC_PCT", 0.60))  # 60%