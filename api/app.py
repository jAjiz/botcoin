import secrets
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

import core.database as db
import core.logging as logging
from api.routes import balance, control, market, positions, status
from core.config import ALLOW_NO_AUTH, API_SECRET_TOKEN, SLEEPING_INTERVAL
from core.scheduler import trading_session
from core.validation import validate_config

scheduler: AsyncIOScheduler | None = None

_api_key_header = APIKeyHeader(name="X-Api-Token", auto_error=False)


def verify_token(x_api_token: str | None = Security(_api_key_header)) -> None:
    if not API_SECRET_TOKEN:
        if ALLOW_NO_AUTH:
            return
        # Defense-in-depth: validate_config should have already failed startup,
        # but if the app somehow boots without a token, refuse every request.
        raise HTTPException(status_code=401, detail="API auth not configured")
    if x_api_token is None or not secrets.compare_digest(x_api_token, API_SECRET_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid or missing API token")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    if not validate_config():
        raise RuntimeError("Configuration validation failed — check logs for details")
    if not db.check_database_connection():
        raise RuntimeError("Cannot connect to PostgreSQL")

    scheduler = AsyncIOScheduler(
        executors={"default": ThreadPoolExecutor(max_workers=1)},
        job_defaults={"max_instances": 1, "coalesce": True},
    )
    scheduler.add_job(
        trading_session,
        trigger=IntervalTrigger(seconds=SLEEPING_INTERVAL),
        next_run_time=datetime.now(),
    )
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=True)
        logging.info("Scheduler stopped.")


app = FastAPI(title="BoTC API", version="0.1.0", lifespan=lifespan)

_auth = [Depends(verify_token)]


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    logging.error(f"Unhandled error in {request.method} {request.url.path}: {exc}")
    return JSONResponse(status_code=500, content={"detail": "internal error"})


@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True}


for _r in (balance, control, market, positions, status):
    app.include_router(_r.router, dependencies=_auth)
