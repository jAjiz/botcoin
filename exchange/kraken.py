import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import krakenex
import pandas as pd

import core.config as config
from core.config import KRAKEN_API_KEY, KRAKEN_API_SECRET

# Kraken allows 1 call/second on public endpoints; the lock enforces it across threads.
KRAKEN_MIN_CALL_INTERVAL_SECONDS = 1.0
_rate_limit_lock = threading.Lock()
_last_public_call_ts = 0.0

# (connect, read) timeout for every call. Must stay finite: krakenex's default of None
# lets a stalled socket block the single scheduler thread forever (it once did).
KRAKEN_HTTP_TIMEOUT: tuple[float, float] = (10.0, 30.0)


class KrakenAPIError(Exception):
    """Raised when the Kraken API returns a non-empty error field."""


def _wait_rate_limit() -> None:
    global _last_public_call_ts

    _rate_limit_lock.acquire()
    try:
        now = time.monotonic()
        remaining = KRAKEN_MIN_CALL_INTERVAL_SECONDS - (now - _last_public_call_ts)
        if remaining > 0:
            time.sleep(remaining)
        _last_public_call_ts = time.monotonic()
    finally:
        _rate_limit_lock.release()


def _query_public_limited(method: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    _wait_rate_limit()
    if data is None:
        return api.query_public(method, timeout=KRAKEN_HTTP_TIMEOUT)
    return api.query_public(method, data, timeout=KRAKEN_HTTP_TIMEOUT)


def _safe_call(label: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
    try:
        response = fn()
        if response.get("error"):
            raise KrakenAPIError(response["error"])
        return response.get("result", {})
    except Exception as e:
        logging.error(f"Error in {label}: {e}")
        return None


api = krakenex.API()
api.key = KRAKEN_API_KEY
api.secret = KRAKEN_API_SECRET


def get_asset_pairs() -> dict[str, Any] | None:
    return _safe_call("asset pairs", lambda: _query_public_limited("AssetPairs"))


def build_pairs_map(pairs_dict: dict[str, dict[str, Any]]) -> None:
    pairs_info = get_asset_pairs()
    if pairs_info is None:
        return
    for primary, info in pairs_info.items():
        altname = info.get("altname", "")
        if altname in pairs_dict:
            pairs_dict[altname] = {
                "primary": primary,
                "wsname": info.get("wsname", ""),
                "base": info.get("base", ""),
                "quote": info.get("quote", ""),
                "pair_decimals": info.get("pair_decimals"),
                "lot_decimals": info.get("lot_decimals"),
                "cost_decimals": info.get("cost_decimals"),
            }
    if not all(pairs_dict[pair] for pair in pairs_dict):
        missing = [pair for pair in pairs_dict if not pairs_dict[pair]]
        for pair in missing:
            del pairs_dict[pair]


def get_balance() -> dict[str, str] | None:
    return _safe_call("balance", lambda: api.query_private("Balance", timeout=KRAKEN_HTTP_TIMEOUT))


@dataclass(frozen=True)
class OrderState:
    status: str
    avg_price: float | None
    vol_exec: float


def get_order_state(order_id: str) -> OrderState | None:
    """Status + average fill price + executed volume of an order, or None on
    API error / unknown order. Callers must branch on ``status`` explicitly —
    never infer completion from a bare price (a canceled order reports 0.0)."""
    result = _safe_call(
        "order state",
        lambda: api.query_private("QueryOrders", {"txid": order_id}, timeout=KRAKEN_HTTP_TIMEOUT),
    )
    if result is None:
        return None
    order = result.get(order_id)
    if not order or not order.get("status"):
        return None
    price = order.get("price")
    return OrderState(
        status=order["status"],
        avg_price=float(price) if price is not None else None,
        vol_exec=float(order.get("vol_exec") or 0.0),
    )


def cancel_order(order_id: str) -> bool:
    """Cancel an open order. False on API error, and also on a response that
    doesn't confirm an immediate, definitive cancellation: ``count: 0`` (nothing
    was actually canceled) or ``pending: true`` (cancellation queued but the order
    can still fill). Either shape must be treated as 'still live' — the caller
    (``reprice_closing_order``) only re-places a new order once the old one is
    confirmed dead, to avoid two live exit orders for one position."""
    result = _safe_call(
        "cancel order",
        lambda: api.query_private("CancelOrder", {"txid": order_id}, timeout=KRAKEN_HTTP_TIMEOUT),
    )
    return bool(result) and int(result.get("count", 0)) > 0 and not result.get("pending")


def get_last_prices(pairs_dict: dict[str, dict[str, Any]]) -> dict[str, float] | None:
    result = _safe_call(
        "current prices",
        lambda: _query_public_limited("Ticker", {"pair": ",".join(pairs_dict.keys())}),
    )
    if result is None:
        return None
    prices = {}
    for pair, info in pairs_dict.items():
        primary = info.get("primary")
        ticker = result.get(primary) if primary else None
        if not ticker:
            logging.warning(f"Ticker response missing {pair} (primary={primary!r}); skipping this pair this session.")
            continue
        prices[pair] = float(ticker["c"][0])
    return prices or None


def _format_amount(value: float, decimals: int | None) -> str:
    """Format a price or volume to the pair's Kraken precision.

    When ``decimals`` is unknown (pair metadata not loaded) the value is sent
    unrounded so we never silently coarsen it — better a possible Kraken reject
    (handled by ``_safe_call``) than a corrupted order price."""
    if decimals is None:
        return str(value)
    return f"{value:.{decimals}f}"


def place_limit_order(pair: str, side: str, price: float, volume: float) -> str | None:
    meta = config.PAIRS.get(pair, {})
    price_str = _format_amount(price, meta.get("pair_decimals"))
    volume_str = _format_amount(volume, meta.get("lot_decimals"))
    result = _safe_call(
        f"{side.upper()} limit order",
        lambda: api.query_private(
            "AddOrder",
            {
                "pair": pair,
                "type": side,
                "ordertype": "limit",
                "price": price_str,
                "volume": volume_str,
            },
            timeout=KRAKEN_HTTP_TIMEOUT,
        ),
    )
    if result is None:
        return None
    new_order = result.get("txid", [None])[0]
    logging.info(f"Created LIMIT {side.upper()} order {new_order} | {volume_str} @ {price_str}€")
    return new_order


def fetch_ohlc_data(pair: str, interval: int, since: int | None = None) -> tuple[pd.DataFrame, int] | None:
    data: dict[str, Any] = {"pair": pair, "interval": interval}
    if since is not None:
        data["since"] = since
    result = _safe_call(f"OHLC data for {pair}", lambda: _query_public_limited("OHLC", data))
    if result is None:
        return None
    last = int(result["last"])
    result_pair = next(k for k in result if k != "last")
    ohlc = pd.DataFrame(result[result_pair])
    if ohlc.empty:
        return ohlc, last
    ohlc.columns = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "vwap",
        "volume",
        "count",
    ]
    ohlc["time"] = pd.to_numeric(ohlc["time"]).astype(int)
    ohlc["dtime"] = pd.to_datetime(ohlc["time"], unit="s")
    for col in ["open", "high", "low", "close", "vwap", "volume"]:
        ohlc[col] = ohlc[col].astype(float)
    ohlc.sort_values("time", ascending=False, inplace=True)
    return ohlc, last
