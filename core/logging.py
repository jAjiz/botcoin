import logging
import os
from logging.handlers import TimedRotatingFileHandler

import httpx

from core.config import TELEGRAM_ENABLED

os.makedirs("logs", exist_ok=True)
file_handler = TimedRotatingFileHandler(
    filename="logs/BoTC.log",
    when="midnight",
    interval=1,
    backupCount=7,
    encoding="utf-8",
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[file_handler, logging.StreamHandler()],
)

TELEGRAM_SERVICE_URL = os.getenv("TELEGRAM_SERVICE_URL")


def _notify(level: str, msg: str) -> None:
    if not TELEGRAM_ENABLED or not TELEGRAM_SERVICE_URL:
        return
    try:
        httpx.post(
            f"{TELEGRAM_SERVICE_URL}/notify",
            json={"message": msg, "level": level},
            timeout=2.0,
        )
    except Exception as e:
        logging.warning(f"Telegram notify failed: {e}")


def info(msg, to_telegram=False):
    logging.info(msg)
    if to_telegram:
        _notify("info", msg)


def warning(msg, to_telegram=False):
    logging.warning(msg)
    if to_telegram:
        _notify("warning", msg)


def error(msg, to_telegram=False):
    logging.error(msg)
    if to_telegram:
        _notify("error", msg)
