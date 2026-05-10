import logging

from core.config import (
    ALLOW_NO_AUTH,
    API_SECRET_TOKEN,
    ATR_DESV_LIMIT,
    ATR_PERIOD,
    CANDLE_TIMEFRAME,
    KRAKEN_API_KEY,
    KRAKEN_API_SECRET,
    PAIRS,
    PARAM_SESSIONS,
    SLEEPING_INTERVAL,
    TELEGRAM_ENABLED,
    TELEGRAM_POLL_INTERVAL,
    TELEGRAM_TOKEN,
    TELEGRAM_USER_ID,
)
from exchange.kraken import build_pairs_map


def validate_common_params(errors: list[str]) -> None:
    # Kraken API credentials
    if not KRAKEN_API_KEY:
        errors.append("KRAKEN_API_KEY is missing")
    if not KRAKEN_API_SECRET:
        errors.append("KRAKEN_API_SECRET is missing")

    # Telegram Bot configuration (only when Telegram is enabled)
    if TELEGRAM_ENABLED:
        if not TELEGRAM_TOKEN:
            errors.append("TELEGRAM_TOKEN is missing")
        if not TELEGRAM_USER_ID or not TELEGRAM_USER_ID.isdigit() or int(TELEGRAM_USER_ID) <= 0:
            errors.append("TELEGRAM_USER_ID must be a positive integer")
        if TELEGRAM_POLL_INTERVAL < 0:
            errors.append("TELEGRAM_POLL_INTERVAL must be a non-negative integer")

    # API auth: refuse to start with no token unless explicit opt-in.
    if not API_SECRET_TOKEN and not ALLOW_NO_AUTH:
        errors.append(
            "API_SECRET_TOKEN is missing. Set it, or set ALLOW_NO_AUTH=true "
            "to explicitly run the API without authentication."
        )

    # Bot settings
    if SLEEPING_INTERVAL <= 0:
        errors.append("SLEEPING_INTERVAL must be a positive integer")
    if PARAM_SESSIONS <= 0:
        errors.append("PARAM_SESSIONS must be a positive integer")
    if CANDLE_TIMEFRAME <= 0:
        errors.append("CANDLE_TIMEFRAME must be a positive integer")
    if ATR_PERIOD <= 0:
        errors.append("ATR_PERIOD must be a positive integer")
    if ATR_DESV_LIMIT < 0:
        errors.append("ATR_DESV_LIMIT must be a non-negative float")

    # Pairs configuration
    if not PAIRS or not any(PAIRS.keys()):
        errors.append("PAIRS is missing or empty")


def build_and_validate_pairs(errors: list[str]) -> None:
    try:
        build_pairs_map(PAIRS)
        if not any(PAIRS.values()):
            errors.append("No valid pairs found")
    except Exception as e:
        errors.append(f"Failed to fetch pairs: {e!s}")


def log_configuration_summary() -> None:
    logging.info("=" * 60)
    logging.info("✅ CONFIGURATION VALIDATED SUCCESSFULLY")
    logging.info("=" * 60)
    logging.info(f"Telegram polling interval: {TELEGRAM_POLL_INTERVAL}s")
    logging.info(f"Session interval: {SLEEPING_INTERVAL}s")
    logging.info(f"Parameter calculation sessions: {PARAM_SESSIONS}")
    logging.info(f"Candle timeframe: {CANDLE_TIMEFRAME}min")
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
        logging.error("=" * 60)
        logging.error("❌ CONFIGURATION VALIDATION FAILED")
        logging.error("=" * 60)
        for error in errors:
            logging.error(f"  - {error}")
        logging.error("=" * 60)
        return False

    # If all validations passed, log configuration summary
    log_configuration_summary()
    return True
