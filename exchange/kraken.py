import logging
import threading
import time

import krakenex
import pandas as pd

from core.config import KRAKEN_API_KEY, KRAKEN_API_SECRET

## Kraken API rate limit: 1 call per second for public endpoints.
# We implement a simple locking mechanism to ensure we respect this limit across all threads.
KRAKEN_MIN_CALL_INTERVAL_SECONDS = 1.0
_rate_limit_lock = threading.Lock()
_last_public_call_ts = 0.0


def _wait_rate_limit():
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


def _query_public_limited(method, data=None):
    _wait_rate_limit()
    if data is None:
        return api.query_public(method)
    return api.query_public(method, data)


api = krakenex.API()
api.key = KRAKEN_API_KEY
api.secret = KRAKEN_API_SECRET


def get_asset_pairs():
    try:
        response = _query_public_limited("AssetPairs")
        if response.get("error"):
            raise Exception(response["error"])
        return response.get("result", {})
    except Exception as e:
        logging.error(f"Error fetching asset pairs: {e}")
        return None


def build_pairs_map(pairs_dict):
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
            }
    if not all(pairs_dict[pair] for pair in pairs_dict):
        missing = [pair for pair in pairs_dict if not pairs_dict[pair]]
        for pair in missing:
            del pairs_dict[pair]


def get_balance():
    try:
        response = api.query_private("Balance")
        if response.get("error"):
            raise Exception(response["error"])
        return response.get("result", {})
    except Exception as e:
        logging.error(f"Error fetching balance: {e}")
        return None


def get_order_status(order_id):
    try:
        response = api.query_private("QueryOrders", {"txid": order_id})
        if response.get("error"):
            raise Exception(response["error"])
        result = response.get("result", {})
        return result.get(order_id, {}).get("status")
    except Exception as e:
        logging.error(f"Error fetching order status for {order_id}: {e}")
        return None


def get_last_prices(pairs_dict):
    try:
        response = _query_public_limited("Ticker", {"pair": ",".join(pairs_dict.keys())})
        if response.get("error"):
            raise Exception(response["error"])
        prices = {}
        for pair, info in pairs_dict.items():
            prices[pair] = round(float(response["result"][info["primary"]]["c"][0]), 1)  # 'c' = last trade price
        return prices
    except Exception as e:
        logging.error(f"Error fetching current prices: {e}")
        return None


def place_limit_order(pair, side, price, volume):
    try:
        response = api.query_private(
            "AddOrder",
            {
                "pair": pair,
                "type": side,
                "ordertype": "limit",
                "price": str(round(price, 1)),
                "volume": str(volume),
            },
        )
        if response.get("error"):
            raise Exception(response["error"])
        new_order = response.get("result", {}).get("txid", [None])[0]
        logging.info(f"Created LIMIT {side.upper()} order {new_order} | {volume:.8f} BTC @ {price:,.1f}€)")
        return new_order
    except Exception as e:
        logging.error(f"Error creating {side.upper()} order: {e}")
        return None


def fetch_ohlc_data(pair, interval, since=None):
    data = {"pair": pair, "interval": interval}
    if since is not None:
        data["since"] = since
    try:
        response = _query_public_limited("OHLC", data)
        if response.get("error"):
            raise Exception(response["error"])
        result_pair = next(iter(response["result"].keys()))
        ohlc = pd.DataFrame(response["result"][result_pair])
        if ohlc.empty:
            return ohlc
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
        ohlc["dtime"] = pd.to_datetime(pd.to_numeric(ohlc["time"]), unit="s")
        for col in ["open", "high", "low", "close", "vwap", "volume"]:
            ohlc[col] = ohlc[col].astype(float)
        ohlc.sort_values("time", ascending=False, inplace=True)
        return ohlc
    except Exception as e:
        logging.error(f"Error fetching OHLC data for {pair}: {e}")
        return None
