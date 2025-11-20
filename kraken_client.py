import krakenex
from pykrakenapi import KrakenAPI
from config import KRAKEN_API_KEY, KRAKEN_API_SECRET, logging

## Ignore future warnings
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

api = krakenex.API()
api.key = KRAKEN_API_KEY
api.secret = KRAKEN_API_SECRET
krakenapi = KrakenAPI(api)

def get_balance():
    return api.query_private("Balance")

def get_open_orders():
    try:
        response = api.query_private("OpenOrders")
        if "error" in response and response["error"]:
            raise Exception(response["error"])
        open_orders = response.get("result", {}).get("open", {})
        return open_orders
    except Exception as e:
        logging.error(f"Error fetching open orders: {e}")
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

def get_current_price(pair="XXBTZEUR"):
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
        "price": round(price, 1),
        "volume": volume,
        })
        if "error" in response and response["error"]:
            raise Exception(response["error"])
        new_order = response.get('result', {}).get('txid', [None])[0]
        logging.info(f"Created LIMIT {side.upper()} order {new_order} | {volume:.8f} BTC @ {price:,.1f}€)")
        return response
    except Exception as e:
        logging.error(f"Error creating {side.upper()} order: {e}")

def place_take_profit_limit(pair, side, trigger_price, limit_price, volume):
    try:
        response = api.query_private("AddOrder", {
            "pair": pair,
            "type": side,
            "ordertype": "take-profit-limit",
            "price": str(round(trigger_price, 1)),
            "price2": str(round(limit_price, 1)),
            "volume": str(volume)
        })
        if "error" in response and response["error"]:
            raise Exception(response["error"])
        new_order = response.get('result', {}).get('txid', [None])[0]
        logging.info(f"Created TP-LIMIT {side.upper()} order {new_order} | {volume:.8f} BTC @ trigger {trigger_price:,.1f}€ (limit {limit_price:,.1f}€)")
        return response
    except Exception as e:
        logging.error(f"Error creating {side.upper()} order: {e}")

def cancel_order(order_id):
    try:
        response = api.query_private("CancelOrder", {"txid": order_id})
        if "error" in response and response["error"]:
            raise Exception(response["error"])
        logging.info(f"Cancelled order {order_id}")
        return response
    except Exception as e:
        logging.error(f"Error cancelling order {order_id}: {e}")
    
def get_current_atr(interval=15, period=14):
    try:
        df, _ = krakenapi.get_ohlc_data("XXBTZEUR", interval=interval)
        df = df.sort_index()

        df["H-L"]  = df["high"] - df["low"]
        df["H-PC"] = (df["high"] - df["close"].shift(1)).abs()
        df["L-PC"] = (df["low"] - df["close"].shift(1)).abs()
        df["TR"] = df[["H-L", "H-PC", "L-PC"]].max(axis=1)
        df["ATR"] = df["TR"].rolling(period).mean()

        return df["ATR"].iloc[-1]
    except Exception as e:
        logging.error(f"Error getting ATR: {e}")
        return None

if __name__ == "__main__":
    print(get_balance())