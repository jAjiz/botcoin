import krakenex
from config import KRAKEN_API_KEY, KRAKEN_API_SECRET, logging

api = krakenex.API()
api.key = KRAKEN_API_KEY
api.secret = KRAKEN_API_SECRET

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
        return e

def get_closed_orders(start, closed_after):
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
        return e

def get_current_price(pair="XXBTZEUR"):
    response = api.query_public("Ticker", {"pair": pair})
    return float(response["result"][pair]["c"][0])  # 'c' = last trade price

def place_limit_order(pair, side, price, volume):
    return api.query_private("AddOrder", {
        "pair": pair,
        "type": side,
        "ordertype": "limit",
        "price": price,
        "volume": volume,
    })

def place_take_profit_limit(pair, side, trigger_price, limit_price, volume):
    return api.query_private("AddOrder", {
        "pair": pair,
        "type": side,
        "ordertype": "take-profit-limit",
        "price": str(round(trigger_price, 1)),
        "price2": str(round(limit_price, 1)),
        "volume": str(volume)
    })

def cancel_order(order_id):
    return api.query_private("CancelOrder", {"txid": order_id})

if __name__ == "__main__":
    print(get_open_orders())