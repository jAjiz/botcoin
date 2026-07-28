import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from core.config import (
    ALLOW_NO_AUTH,
    API_SECRET_TOKEN,
    TELEGRAM_ENABLED,
    TELEGRAM_POLL_INTERVAL,
    TELEGRAM_TOKEN,
    TELEGRAM_USER_ID,
)
from core.logging import configure_logging
from services.telegram.polling import build_tg_app
from telegram.ext import Application

configure_logging()

tg_app: Application | None = None

PREFIX = {"info": "", "warning": "⚠️ ", "error": "❌ "}


class NotifyRequest(BaseModel):
    message: str
    level: Literal["info", "warning", "error"] = "info"


def _validate_telegram_config() -> None:
    """Mirrors the Telegram checks in core.validation.validate_common_params,
    which only ever run in the botc process — this service must not rely on
    that having caught a misconfiguration. Without this, a missing/invalid
    TELEGRAM_TOKEN or TELEGRAM_USER_ID would surface later and less clearly
    (e.g. int(None) deep inside build_tg_app/send_message)."""
    if not TELEGRAM_ENABLED:
        return
    errors = []
    if not TELEGRAM_TOKEN:
        errors.append("TELEGRAM_TOKEN is missing")
    if not TELEGRAM_USER_ID or not TELEGRAM_USER_ID.isdigit() or int(TELEGRAM_USER_ID) <= 0:
        errors.append("TELEGRAM_USER_ID must be a positive integer")
    if TELEGRAM_POLL_INTERVAL < 0:
        errors.append("TELEGRAM_POLL_INTERVAL must be a non-negative integer")
    if errors:
        raise RuntimeError("Telegram service configuration invalid: " + "; ".join(errors))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global tg_app
    _validate_telegram_config()
    if not TELEGRAM_ENABLED:
        yield
        return
    tg_app = build_tg_app()
    try:
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling(poll_interval=TELEGRAM_POLL_INTERVAL)
        await tg_app.bot.send_message(
            chat_id=int(TELEGRAM_USER_ID),
            text="🤖 BoTC started and running. Use /help to see available commands.",
        )
        yield
    finally:
        try:
            await tg_app.bot.send_message(
                chat_id=int(TELEGRAM_USER_ID),
                text="🤖 BoTC is off, see you soon",
            )
        except Exception as e:
            logging.warning(f"Telegram shutdown notice failed: {e}")
        if tg_app.updater is not None and tg_app.updater.running:
            await tg_app.updater.stop()
        if tg_app.running:
            await tg_app.stop()
        await tg_app.shutdown()


app = FastAPI(title="BoTC Telegram", version="0.1.0", lifespan=lifespan)


@app.post("/notify", status_code=202)
async def notify(req: NotifyRequest, x_api_token: str | None = Header(default=None)) -> dict[str, bool]:
    if API_SECRET_TOKEN:
        if x_api_token is None or not secrets.compare_digest(x_api_token, API_SECRET_TOKEN):
            raise HTTPException(status_code=401, detail="Invalid or missing API token")
    elif not ALLOW_NO_AUTH:
        raise HTTPException(status_code=401, detail="API auth not configured")
    if tg_app is None:
        return {"accepted": False, "reason": "Telegram is disabled"}
    try:
        await tg_app.bot.send_message(
            chat_id=int(TELEGRAM_USER_ID),
            text=PREFIX[req.level] + req.message,
        )
    except Exception as e:
        logging.error(f"Telegram send failed: {e}")
    return {"accepted": True}
