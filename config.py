from dotenv import load_dotenv
import os
import logging

# Load .env variables
load_dotenv()
MAX_OPEN_SELLS = os.getenv("MAX_OPEN_SELLS")
MARGIN = os.getenv("MARGIN")
LIMIT_BUFFER = os.getenv("LIMIT_BUFFER")
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET")

# Logging configuration
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("logs/BoTC.log", encoding='utf-8'), logging.StreamHandler()]
)