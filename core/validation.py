import logging
from exchange.kraken import build_pairs_map
from core.config import (
    KRAKEN_API_KEY,
    KRAKEN_API_SECRET,
    TELEGRAM_TOKEN,
    ALLOWED_USER_ID,
    MODE,
    PAIRS,
    TRADING_PARAMS,
    ASSET_MIN_ALLOCATION,
    POLL_INTERVAL_SEC,
    SLEEPING_INTERVAL,
    ATR_DATA_DAYS,
    ATR_INTERVAL,
    ATR_PERIOD
)

def validate_common_params(errors):
    if not KRAKEN_API_KEY:
        errors.append("KRAKEN_API_KEY is missing")
    if not KRAKEN_API_SECRET:
        errors.append("KRAKEN_API_SECRET is missing")

    if not TELEGRAM_TOKEN:
        errors.append("TELEGRAM_TOKEN is missing")
    if not ALLOWED_USER_ID:
        errors.append("ALLOWED_USER_ID is missing")

    if not MODE:
        errors.append("MODE is missing")
    elif MODE not in ["onek", "dualk"]:
        errors.append(f"Invalid MODE '{MODE}'. Must be 'onek' or 'dualk'")

    if not PAIRS or not any(PAIRS.keys()):
        errors.append("PAIRS is missing or empty")

def build_and_validate_pairs(errors):
    try:
        build_pairs_map(PAIRS)
        if not any(PAIRS.values()):
            errors.append("No valid pairs found")
    except Exception as e:
        errors.append(f"Failed to fetch pairs: {str(e)}")
    
def validate_onek_params(errors):    
    for pair in PAIRS.keys():
        params = TRADING_PARAMS.get(pair, {})
        
        # Validate SELL side
        sell_params = params.get("sell", {})
        sell_k_stop = sell_params.get("K_STOP", -1)
        sell_min_margin = sell_params.get("MIN_MARGIN", -1)
        
        if sell_k_stop < 0:
            errors.append(f"{pair}_SELL_K_STOP is missing or invalid")
        if sell_min_margin < 0:
            errors.append(f"{pair}_SELL_MIN_MARGIN is missing or invalid")
        
        # Validate BUY side
        buy_params = params.get("buy", {})
        buy_k_stop = buy_params.get("K_STOP", -1)
        buy_min_margin = buy_params.get("MIN_MARGIN", -1)

        if buy_k_stop < 0:
            errors.append(f"{pair}_BUY_K_STOP is missing or invalid")
        if buy_min_margin < 0:
            errors.append(f"{pair}_BUY_MIN_MARGIN is missing or invalid")
    
def validate_dualk_params(errors):
    for pair in PAIRS.keys():
        params = TRADING_PARAMS.get(pair, {})
        
        # Validate SELL side
        sell_params = params.get("sell", {})
        sell_k_act = sell_params.get("K_ACT", -1)
        sell_k_stop = sell_params.get("K_STOP", -1)
        sell_min_margin = sell_params.get("MIN_MARGIN", -1)
        
        if sell_k_act < 0:
            errors.append(f"{pair}_SELL_K_ACT is missing or invalid")
        if sell_k_stop < 0:
            errors.append(f"{pair}_SELL_K_STOP is missing or invalid")
        if sell_min_margin < 0:
            errors.append(f"{pair}_SELL_MIN_MARGIN is missing or invalid")
        
        # Check K_ACT > K_STOP for valid ATR_MIN calculation
        if sell_k_act <= sell_k_stop:
            errors.append(f"{pair}_SELL_K_ACT ({sell_k_act}) must be > {pair}_SELL_K_STOP ({sell_k_stop})")
        else:
            sell_params["ATR_MIN"] = sell_min_margin / (sell_k_act - sell_k_stop)
        
        # Validate BUY side
        buy_params = params.get("buy", {})
        buy_k_act = buy_params.get("K_ACT", -1)
        buy_k_stop = buy_params.get("K_STOP", -1)
        buy_min_margin = buy_params.get("MIN_MARGIN", -1)
        
        if buy_k_act < 0:
            errors.append(f"{pair}_BUY_K_ACT is missing or invalid")
        if buy_k_stop < 0:
            errors.append(f"{pair}_BUY_K_STOP is missing or invalid")
        if buy_min_margin < 0:
            errors.append(f"{pair}_BUY_MIN_MARGIN is missing or invalid")
        
        # Check K_ACT > K_STOP for valid ATR_MIN calculation
        if buy_k_act <= buy_k_stop:
            errors.append(f"{pair}_BUY_K_ACT ({buy_k_act}) must be > {pair}_BUY_K_STOP ({buy_k_stop})")
        else:
            buy_params["ATR_MIN"] = buy_min_margin / (buy_k_act - buy_k_stop)

def log_configuration_summary():
    logging.info("=" * 60)
    logging.info("✅ CONFIGURATION VALIDATED SUCCESSFULLY")
    logging.info("=" * 60)
    logging.info(f"Mode: {MODE}")
    logging.info(f"Session interval: {SLEEPING_INTERVAL}s")
    logging.info(f"Telegram polling interval: {POLL_INTERVAL_SEC}s")
    logging.info(f"ATR: {ATR_INTERVAL}min candles | {ATR_PERIOD} period | {ATR_DATA_DAYS} days data")
    logging.info("-" * 60)
    
    # Trading parameters per pair
    for pair in PAIRS.keys():
        params = TRADING_PARAMS[pair]
        logging.info(f"[{pair}] Trading Parameters:")
        
        # SELL side - log all available keys for this mode
        sell = params["sell"]
        sell_str = ", ".join([f"{k}={v}" for k, v in sell.items()])
        logging.info(f"  SELL: {sell_str}")
        
        # BUY side - log all available keys for this mode
        buy = params["buy"]
        buy_str = ", ".join([f"{k}={v}" for k, v in buy.items()])
        logging.info(f"  BUY:  {buy_str}")

        logging.info(f"  MIN ALLOCATION:  {ASSET_MIN_ALLOCATION[pair]:.0%}")

def validate_config() -> bool:
    errors = []
    
    # Common validations
    validate_common_params(errors)
    
    if not errors:
        build_and_validate_pairs(errors)
    
    if not errors:
        # Mode-specific validations
        if MODE == "onek":
            validate_onek_params(errors)
        elif MODE == "dualk":
            validate_dualk_params(errors)
    
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
