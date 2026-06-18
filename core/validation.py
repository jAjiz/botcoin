import logging
from typing import Any

import core.config as config
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
    STOP_PCT_DEFAULT,
    TELEGRAM_ENABLED,
    TELEGRAM_POLL_INTERVAL,
    TELEGRAM_TOKEN,
    TELEGRAM_USER_ID,
    VOLATILITY_LEVELS,
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


def _parse_float(
    value: Any,
    name: str,
    errors: list[str],
    *,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float | None:
    """Parse value to float. Empty/None returns None."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        errors.append(f"{name} must be a float (got {value!r})")
        return None
    if min_val is not None and f < min_val:
        errors.append(f"{name} must be >= {min_val} (got {f})")
        return None
    if max_val is not None and f > max_val:
        errors.append(f"{name} must be <= {max_val} (got {f})")
        return None
    return f


def normalize_pair_config(pair: str, raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate + normalize one pair's flat config.

    ``raw`` keys: k_act, min_margin, target_pct, hodl_pct, stop_pct_ll..stop_pct_hh
    (values may be strings, floats, or None). Returns (typed_flat, errors).
    Rules:
    - k_act: float >= 0, or None (falls through to K_STOP + MIN_MARGIN path).
    - min_margin: float >= 0; required when k_act is None; normalized to 0.0 when
      k_act is set.
    - target_pct, hodl_pct: float in [0, 100] (default 0.0 when unset).
    - stop_pct_<level>: float in [0, 1] (default STOP_PCT_DEFAULT when unset).
    The cross-pair target sum is checked separately by target_sum_error.
    """
    errors: list[str] = []
    out: dict[str, Any] = {}

    k_act = _parse_float(raw.get("k_act"), f"{pair}_K_ACT", errors, min_val=0)
    out["k_act"] = k_act

    mm_raw = raw.get("min_margin")
    if k_act is None:
        min_margin = _parse_float(mm_raw, f"{pair}_MIN_MARGIN", errors, min_val=0)
        if min_margin is None and mm_raw in (None, ""):
            errors.append(f"{pair}_MIN_MARGIN is required when K_ACT is not set")
        out["min_margin"] = min_margin
    else:
        parsed = _parse_float(mm_raw, f"{pair}_MIN_MARGIN", [], min_val=0)
        out["min_margin"] = parsed if parsed is not None else 0.0

    target = _parse_float(raw.get("target_pct"), f"{pair}_TARGET_PCT", errors, min_val=0, max_val=100)
    out["target_pct"] = target if target is not None else 0.0

    hodl = _parse_float(raw.get("hodl_pct"), f"{pair}_HODL_PCT", errors, min_val=0, max_val=100)
    out["hodl_pct"] = hodl if hodl is not None else 0.0

    for level in VOLATILITY_LEVELS:
        key = f"stop_pct_{level.lower()}"
        parsed = _parse_float(raw.get(key), f"{pair}_STOP_PCT_{level}", errors, min_val=0, max_val=1)
        out[key] = parsed if parsed is not None else STOP_PCT_DEFAULT

    return out, errors


def target_sum_error(targets: dict[str, float]) -> str | None:
    """Return an error string if the sum of target_pct across pairs exceeds 100."""
    total = sum(targets.values())
    if total > 100:
        return f"Sum of TARGET_PCT across all pairs must not exceed 100 (got {total:g})"
    return None


def validate_pair_params(errors: list[str]) -> None:
    """Validate and normalize per-pair trading parameters, writing typed values
    back into the live dicts. Shares normalize_pair_config with the runtime
    config store so startup and runtime apply identical rules."""
    typed_targets: dict[str, float] = {}
    for pair in config.PAIRS:
        raw = config.get_pair_config(pair)
        typed, errs = normalize_pair_config(pair, raw)
        errors.extend(errs)
        config.set_pair_config(pair, typed)
        typed_targets[pair] = typed["target_pct"]

    err = target_sum_error(typed_targets)
    if err:
        errors.append(err)


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
        validate_pair_params(errors)

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
