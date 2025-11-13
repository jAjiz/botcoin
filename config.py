import os
import logging
from dotenv import load_dotenv
from logging.handlers import TimedRotatingFileHandler

# Load .env variables
load_dotenv()
MARGIN = float(os.getenv("MARGIN"))
TRAILING_DISTANCE = float(os.getenv("TRAILING_DISTANCE"))
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET")

# Logging configuration
os.makedirs("logs", exist_ok=True)
file_handler = TimedRotatingFileHandler(
    filename="logs/BoTC.log",
    when="midnight",
    interval=1,
    backupCount=7,
    encoding="utf-8"
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[file_handler, logging.StreamHandler()]
)