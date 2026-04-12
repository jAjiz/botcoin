import logging
from exchange.kraken import build_pairs_map
from core.config import (
    KRAKEN_API_KEY,
    KRAKEN_API_SECRET,
    TELEGRAM_ENABLED,
    TELEGRAM_TOKEN,
    TELEGRAM_USER_ID,
    TELEGRAM_POLL_INTERVAL,
    SLEEPING_INTERVAL,
    PARAM_SESSIONS,
    CANDLE_TIMEFRAME,
    MARKET_DATA_DAYS,
    ATR_PERIOD,
    ATR_DESV_LIMIT,
    PAIRS
)

def validate_common_params(errors):
    # Kraken API credentials
    if not KRAKEN_API_KEY:
        errors.append("KRAKEN_API_KEY is missing")
    if not KRAKEN_API_SECRET:
        errors.append("KRAKEN_API_SECRET is missing")

    # Telegram Bot configuration (only when Telegram is enabled)
    if TELEGRAM_ENABLED:
        if not TELEGRAM_TOKEN:
            errors.append("TELEGRAM_TOKEN is missing")
        if not TELEGRAM_USER_ID:
            errors.append("TELEGRAM_USER_ID is missing")
        elif not TELEGRAM_USER_ID.isdigit() or int(TELEGRAM_USER_ID) <= 0:
            errors.append("TELEGRAM_USER_ID must be a positive integer")
        if TELEGRAM_POLL_INTERVAL < 0:
            errors.append("TELEGRAM_POLL_INTERVAL must be a non-negative integer")

    # Bot settings
    if SLEEPING_INTERVAL <= 0:
        errors.append("SLEEPING_INTERVAL must be a positive integer")
    if PARAM_SESSIONS <= 0:
        errors.append("PARAM_SESSIONS must be a positive integer")
    if CANDLE_TIMEFRAME <= 0:
        errors.append("CANDLE_TIMEFRAME must be a positive integer")
    if MARKET_DATA_DAYS <= 0:
        errors.append("MARKET_DATA_DAYS must be a positive integer")
    if ATR_PERIOD <= 0:
        errors.append("ATR_PERIOD must be a positive integer")
    if ATR_DESV_LIMIT < 0:
        errors.append("ATR_DESV_LIMIT must be a non-negative float")

    # Pairs configuration
    if not PAIRS or not any(PAIRS.keys()):
        errors.append("PAIRS is missing or empty")

def build_and_validate_pairs(errors):
    try:
        build_pairs_map(PAIRS)
        if not any(PAIRS.values()):
            errors.append("No valid pairs found")
    except Exception as e:
        errors.append(f"Failed to fetch pairs: {str(e)}")

def log_configuration_summary():
    logging.info("=" * 60)
    logging.info("✅ CONFIGURATION VALIDATED SUCCESSFULLY")
    logging.info("=" * 60)
    logging.info(f"Telegram polling interval: {TELEGRAM_POLL_INTERVAL}s")
    logging.info(f"Session interval: {SLEEPING_INTERVAL}s")
    logging.info(f"Parameter calculation sessions: {PARAM_SESSIONS}")
    logging.info(f"Candle timeframe: {CANDLE_TIMEFRAME}min")
    logging.info(f"Market data storage: {MARKET_DATA_DAYS} days")
    logging.info(f"ATR period: {ATR_PERIOD} candles")
    logging.info(f"Pairs to trade: {', '.join(PAIRS.keys())}")
    logging.info("-" * 60 + "\n")

def validate_config() -> bool:
    errors = []
    
    # Common validations
    validate_common_params(errors)
    
    if not errors:
        build_and_validate_pairs(errors)
    
    # Log all errors at the end
    if errors:
        logging.error("="*60)
        logging.error("❌ CONFIGURATION VALIDATION FAILED")
        logging.error("="*60)
        for error in errors:
            logging.error(f"  - {error}")
        logging.error("="*60)
        return False
    
    # If all validations passed, log configuration summary
    log_configuration_summary()
    return True
