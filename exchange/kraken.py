import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import krakenex
import pandas as pd

import core.config as config
from core.config import KRAKEN_API_KEY, KRAKEN_API_SECRET

# Kraken allows 1 call/second on public endpoints; the lock enforces it across threads.
KRAKEN_MIN_CALL_INTERVAL_SECONDS = 1.0
_rate_limit_lock = threading.Lock()
_last_public_call_ts = 0.0

# (connect, read) timeout. Must stay finite: krakenex defaults to None and a stalled
# socket once blocked the single scheduler thread forever.
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
                "ordermin": float(info["ordermin"]) if info.get("ordermin") is not None else None,
            }
    if not all(pairs_dict[pair] for pair in pairs_dict):
        missing = [pair for pair in pairs_dict if not pairs_dict[pair]]
        for pair in missing:
            del pairs_dict[pair]


def get_balance() -> dict[str, str] | None:
    return _safe_call("balance", lambda: api.query_private("Balance", timeout=KRAKEN_HTTP_TIMEOUT))


class OrderStatus(StrEnum):
    """Kraken's order vocabulary, normalized so callers never parse raw strings."""

    PENDING = "pending"  # accepted, not on the book yet
    OPEN = "open"  # resting on the book
    CLOSED = "closed"  # fully executed
    CANCELED = "canceled"  # canceled or expired: off the book, no further fills
    NOT_FOUND = "not_found"  # Kraken answered but does not know this order id
    UNKNOWN = "unknown"  # Kraken reported a status this wrapper does not model


_RAW_ORDER_STATUS: dict[str, OrderStatus] = {
    "pending": OrderStatus.PENDING,
    "open": OrderStatus.OPEN,
    "closed": OrderStatus.CLOSED,
    "canceled": OrderStatus.CANCELED,
    "expired": OrderStatus.CANCELED,
}


def map_order_status(raw: str | None) -> OrderStatus:
    """Translate a raw Kraken status; anything unmodelled reads as UNKNOWN."""
    return _RAW_ORDER_STATUS.get(raw or "", OrderStatus.UNKNOWN)


@dataclass(frozen=True)
class OrderState:
    status: OrderStatus
    avg_price: float | None
    vol_exec: float
    # The order's own size. 0.0 when Kraken omits it, so a remainder check fails closed.
    vol: float = 0.0


def _build_order_state(order: dict[str, Any]) -> OrderState:
    """Build an ``OrderState`` from a raw Kraken order, so every lookup path reads it identically."""
    price = order.get("price")
    return OrderState(
        status=map_order_status(order.get("status")),
        avg_price=float(price) if price is not None else None,
        vol_exec=float(order.get("vol_exec") or 0.0),
        vol=float(order.get("vol") or 0.0),
    )


def get_order_state(order_id: str) -> OrderState | None:
    """An order's status, average fill price and volumes; ``None`` only when the API call failed."""
    result = _safe_call(
        "order state",
        lambda: api.query_private("QueryOrders", {"txid": order_id}, timeout=KRAKEN_HTTP_TIMEOUT),
    )
    if result is None:
        return None
    order = result.get(order_id)
    if not order:
        return OrderState(status=OrderStatus.NOT_FOUND, avg_price=None, vol_exec=0.0)
    return _build_order_state(order)


@dataclass(frozen=True)
class OrderLookup:
    txid: str | None  # None when neither endpoint knows the id
    state: OrderState | None  # the matched order, ready for the dispatch


def find_order_by_cl_ord_id(cl_ord_id: str) -> OrderLookup | None:
    """Resolve a client order id to Kraken's txid and state; ``None`` is 'the lookup failed', ``txid=None`` is 'both endpoints answered without it'.

    ``OpenOrders`` first: the loop returns on the first endpoint that matches, so a resting order only wins over a terminal one carrying the same id if its endpoint is asked first.
    Neither call is bounded: any bound from our clock could exclude the very order being resolved.
    """
    unresolved = False  # only ever latched: an endpoint we could not read is never evidence of absence
    for method, result_key in (("OpenOrders", "open"), ("ClosedOrders", "closed")):
        result = _safe_call(
            f"{method} lookup",
            lambda m=method: api.query_private(m, {"cl_ord_id": cl_ord_id}, timeout=KRAKEN_HTTP_TIMEOUT),
        )
        if result is None:
            unresolved = True
            continue
        orders = result.get(result_key) or {}
        matches = [(txid, order) for txid, order in orders.items() if order.get("cl_ord_id") == cl_ord_id]
        if not matches:
            if orders:  # rows that don't echo the id: "not ours" and "the echo is gone" are indistinguishable
                logging.error(f"{method} returned {len(orders)} orders for cl_ord_id {cl_ord_id}, none echoing it.")
                unresolved = True
            continue
        if len(matches) > 1:  # impossible with per-attempt ids; the resting endpoint already went first
            logging.error(f"{method} returned {len(matches)} matches for cl_ord_id {cl_ord_id}; adopting the first.")
        txid, order = matches[0]
        return OrderLookup(txid=txid, state=_build_order_state(order))
    return None if unresolved else OrderLookup(txid=None, state=None)


def cancel_order(order_id: str) -> bool:
    """Cancel an open order; True only on a definitive cancellation (``count > 0``, not ``pending``)."""
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
        try:
            prices[pair] = float(ticker["c"][0])
        except (KeyError, IndexError, TypeError, ValueError) as e:
            # Outside _safe_call and the scheduler's per-pair guard: a raise here kills every pair.
            logging.warning(f"Ticker entry for {pair} is malformed ({e}); skipping this pair this session.")
    return prices or None


def _format_amount(value: float, decimals: int | None) -> str:
    """Format to the pair's Kraken precision; sent unrounded when unknown, never silently coarsened."""
    if decimals is None:
        return str(value)
    return f"{value:.{decimals}f}"


def place_limit_order(pair: str, side: str, price: float, volume: float, cl_ord_id: str | None = None) -> str | None:
    meta = config.PAIRS.get(pair, {})
    price_str = _format_amount(price, meta.get("pair_decimals"))
    volume_str = _format_amount(volume, meta.get("lot_decimals"))
    payload = {
        "pair": pair,
        "type": side,
        "ordertype": "limit",
        "price": price_str,
        "volume": volume_str,
    }
    if cl_ord_id is not None:
        payload["cl_ord_id"] = cl_ord_id
    result = _safe_call(
        f"{side.upper()} limit order",
        lambda: api.query_private("AddOrder", payload, timeout=KRAKEN_HTTP_TIMEOUT),
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
