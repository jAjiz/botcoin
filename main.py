import sys
import signal
from datetime import datetime
import core.logging as logging
import core.database as db
import services.telegram as telegram
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from core.config import SLEEPING_INTERVAL, TELEGRAM_ENABLED
from core.validation import validate_config
from core.scheduler import trading_session


def main():
    if not validate_config():
        sys.exit(1)

    if not db.check_database_connection():
        logging.error("Cannot connect to PostgreSQL. Check DATABASE_URL / POSTGRES_* env vars.")
        sys.exit(1)

    if TELEGRAM_ENABLED:
        telegram.initialize_telegram()

    scheduler = BlockingScheduler()
    scheduler.add_job(
        trading_session,
        trigger=IntervalTrigger(seconds=SLEEPING_INTERVAL),
        max_instances=1,
        next_run_time=datetime.now(),
    )

    def _handle_shutdown(signum, _frame):
        signal_name = signal.Signals(signum).name
        logging.info(f"Received {signal_name}. Shutting down scheduler...")
        try:
            scheduler.shutdown(wait=True)
        except Exception as e:
            logging.error(f"Error while shutting down scheduler: {e}")

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    try:
        scheduler.start()
    except Exception as e:
        logging.error(f"BoTC encountered an error: {e}\n", to_telegram=True)
    finally:
        if TELEGRAM_ENABLED:
            telegram.stop_telegram_thread()
        logging.info("BoTC has stopped.", to_telegram=True)


if __name__ == "__main__":
    main()
