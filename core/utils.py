from datetime import UTC, datetime

import core.config as config

# Used when a pair's metadata isn't loaded (display fallback only; never used to
# size or submit orders).
PRICE_DECIMALS_FALLBACK = 2
VOLUME_DECIMALS_FALLBACK = 8


def now_utc() -> datetime:
    return datetime.now(UTC)


def _round_to_pair_decimals(pair: str, value: float | None, key: str, fallback: int) -> float | None:
    """Round ``value`` to a pair's Kraken precision (``key`` in ``config.PAIRS``).

    The rounding helpers below format the result with a thousands separator at the
    call site (``f"{round_price(pair, x):,}€"``). Rounding happens only at
    boundaries (these, plus order submission in ``exchange/kraken.py``); internal
    state and the DB stay full precision. Reads ``config.PAIRS``, so it only works
    in a process that loaded the pair metadata (the trading engine), not the
    Telegram process — that one relies on the API pre-rounding."""
    if value is None:
        return None
    decimals = config.PAIRS.get(pair, {}).get(key)
    if decimals is None:
        decimals = fallback
    return round(value, decimals)


def round_price(pair: str, value: float | None) -> float | None:
    """Round a price/ATR to the pair's Kraken ``pair_decimals``; pass ``None`` through."""
    return _round_to_pair_decimals(pair, value, "pair_decimals", PRICE_DECIMALS_FALLBACK)


def round_volume(pair: str, value: float | None) -> float | None:
    """Round a volume to the pair's Kraken ``lot_decimals``; pass ``None`` through."""
    return _round_to_pair_decimals(pair, value, "lot_decimals", VOLUME_DECIMALS_FALLBACK)
