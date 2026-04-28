from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

import logging
from core.config import API_SECRET_TOKEN, TELEGRAM_POLL_INTERVAL, TELEGRAM_USER_ID
from services.telegram.polling import build_tg_app

tg_app = None

PREFIX = {"info": "", "warning": "⚠️ ", "error": "❌ "}


class NotifyRequest(BaseModel):
    message: str
    level: Literal["info", "warning", "error"] = "info"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tg_app
    tg_app = build_tg_app()
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling(poll_interval=TELEGRAM_POLL_INTERVAL)
    try:
        yield
    finally:
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()


app = FastAPI(title="BoTC Telegram", version="0.1.0", lifespan=lifespan)


@app.post("/notify", status_code=202)
async def notify(req: NotifyRequest, x_api_token: Optional[str] = Header(default=None)):
    if API_SECRET_TOKEN and x_api_token != API_SECRET_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
    try:
        await tg_app.bot.send_message(
            chat_id=int(TELEGRAM_USER_ID),
            text=PREFIX[req.level] + req.message,
        )
    except Exception as e:
        logging.error(f"Telegram send failed: {e}")
    return {"accepted": True}
