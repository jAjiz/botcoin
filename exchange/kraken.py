import os
import krakenex
import logging
import pandas as pd
from datetime import datetime, timedelta
from pykrakenapi import KrakenAPI
from core.config import KRAKEN_API_KEY, KRAKEN_API_SECRET, ATR_DATA_DAYS

## Ignore future warnings
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

api = krakenex.API()
api.key = KRAKEN_API_KEY
api.secret = KRAKEN_API_SECRET
krakenapi = KrakenAPI(api)

def get_asset_pairs():
    try:
        response = api.query_public("AssetPairs")
        if "error" in response and response["error"]:
            raise Exception(response["error"])
        return response.get("result", {})
    except Exception as e:
        logging.error(f"Error fetching asset pairs: {e}")

def build_pairs_map(pairs_dict):
    pairs_info = get_asset_pairs()
    for primary, info in pairs_info.items():
        altname = info.get('altname', '')
        if altname in pairs_dict:
            pairs_dict[altname] = {
                'primary': primary,
                'wsname': info.get("wsname", ""),
                'base': info.get("base", ""),
                'quote': info.get("quote", "")
            }
    if not all(pairs_dict[pair] for pair in pairs_dict):
        missing = [pair for pair in pairs_dict if not pairs_dict[pair]]
        for pair in missing:
            del pairs_dict[pair]

def get_balance():
    try:
        response = api.query_private("Balance")
        if "error" in response and response["error"]:
            raise Exception(response["error"])
        return response.get("result", {})
    except Exception as e:
        logging.error(f"Error fetching balance: {e}")
        return {}

def get_closed_orders(start=0, closed_after=0):
    try:
        response = api.query_private("ClosedOrders", { "start": start })
        if "error" in response and response["error"]:
            raise Exception(response["error"])
        closed_orders = response.get("result", {}).get("closed", {})
        closed_orders = {
            oid: o for oid, o in closed_orders.items() 
            if o.get("status") == "closed" and o.get("closetm", 0) >= closed_after
        }
        return closed_orders
    except Exception as e:
        logging.error(f"Error fetching closed orders: {e}")
        return {}

def get_last_price(pair):
    try:
        response = api.query_public("Ticker", {"pair": pair})
        if "error" in response and response["error"]:
            raise Exception(response["error"])
        return float(response["result"][pair]["c"][0])  # 'c' = last trade price
    except Exception as e:
        logging.error(f"Error fetching current price for {pair}: {e}")
        return None

def place_limit_order(pair, side, price, volume):
    try:
        response = api.query_private("AddOrder", {
            "pair": pair,
            "type": side,
            "ordertype": "limit",
            "price": str(round(price, 1)),
            "volume": str(volume),
        })
        if "error" in response and response["error"]:
            raise Exception(response["error"])
        new_order = response.get('result', {}).get('txid', [None])[0]
        logging.info(f"Created LIMIT {side.upper()} order {new_order} | {volume:.8f} BTC @ {price:,.1f}€)")
        return new_order
    except Exception as e:
        logging.error(f"Error creating {side.upper()} order: {e}")
    
def get_current_atr(pair, interval=15, period=14):
    try:
        atr_file = f"data/{pair}_atr_data_{interval}min.csv"
        since_param = None
        existing_df = None
        
        if os.path.exists(atr_file):
            try:
                existing_df = pd.read_csv(atr_file, index_col=0, parse_dates=True)
                if not existing_df.empty:
                    last_timestamp = int(existing_df.index[-1].timestamp())
                    since_param = last_timestamp
            except Exception as e:
                existing_df = None
        
        df, _ = krakenapi.get_ohlc_data(pair, interval=interval, since=since_param)
        df = df.sort_index()
        
        if existing_df is not None and not existing_df.empty:
            df = pd.concat([existing_df, df])
            df = df[~df.index.duplicated(keep='last')]
            df = df.sort_index()
        
        cutoff_date = datetime.now() - timedelta(days=ATR_DATA_DAYS)
        df = df[df.index >= cutoff_date]
        
        df["H-L"] = df["high"] - df["low"]
        df["H-PC"] = (df["high"] - df["close"].shift(1)).abs()
        df["L-PC"] = (df["low"] - df["close"].shift(1)).abs()
        df["TR"] = df[["H-L", "H-PC", "L-PC"]].max(axis=1)
        df["ATR"] = df["TR"].rolling(period).mean()       
        df.to_csv(atr_file)
        
        current_atr = df["ATR"].iloc[-1]
        return current_atr
    except Exception as e:
        logging.error(f"Error getting ATR for {pair}: {e}")
        return None

if __name__ == "__main__":
    print(get_balance())