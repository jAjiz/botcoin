import os
import logging
from logging.handlers import TimedRotatingFileHandler
import telegram_interface

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

def log_info(msg, to_telegram=False):
    logging.info(msg)
    if to_telegram:
        telegram_interface.send_notification(msg)

def log_warning(msg, to_telegram=False):
    logging.warning(msg)
    if to_telegram:
        telegram_interface.send_notification("⚠️ " + msg)

def log_error(msg, to_telegram=False):
    logging.error(msg)
    if to_telegram:
        telegram_interface.send_notification("❌ " + msg)