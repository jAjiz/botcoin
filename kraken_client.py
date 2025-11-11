import krakenex
from config import KRAKEN_API_KEY, KRAKEN_API_SECRET

api = krakenex.API()
api.key = KRAKEN_API_KEY
api.secret = KRAKEN_API_SECRET

def get_balance():
    return api.query_private("Balance")

def get_open_orders():
    return api.query_private("OpenOrders")

def get_closed_orders(start=0):
    return api.query_private("ClosedOrders", { "start": start })

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

if __name__ == "__main__":
    print(get_open_orders())