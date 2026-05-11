import logging
import os

import httpx

from core.config import API_SECRET_TOKEN, TELEGRAM_ENABLED


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
        force=True,
    )
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True


configure_logging()

TELEGRAM_SERVICE_URL = os.getenv("TELEGRAM_SERVICE_URL")


def _notify(level: str, msg: str) -> None:
    if not TELEGRAM_ENABLED or not TELEGRAM_SERVICE_URL:
        return
    try:
        headers = {"X-Api-Token": API_SECRET_TOKEN} if API_SECRET_TOKEN else {}
        httpx.post(
            f"{TELEGRAM_SERVICE_URL}/notify",
            json={"message": msg, "level": level},
            headers=headers,
            timeout=2.0,
        )
    except Exception as e:
        logging.warning(f"Telegram notify failed: {e}")


def info(msg: str, to_telegram: bool = False) -> None:
    logging.info(msg)
    if to_telegram:
        _notify("info", msg)


def warning(msg: str, to_telegram: bool = False) -> None:
    logging.warning(msg)
    if to_telegram:
        _notify("warning", msg)


def error(msg: str, to_telegram: bool = False) -> None:
    logging.error(msg)
    if to_telegram:
        _notify("error", msg)
