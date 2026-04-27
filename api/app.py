from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import core.database as db
import core.logging as logging
from core.config import SLEEPING_INTERVAL
from core.scheduler import trading_session

scheduler: AsyncIOScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
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


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    logging.error(f"Unhandled error in {request.method} {request.url.path}: {exc}")
    return JSONResponse(status_code=500, content={"detail": "internal error"})


from api.routes import balance, control, market, positions, status  # noqa: E402

for r in (market, positions, balance, status, control):
    app.include_router(r.router)
