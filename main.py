import time
from config import logging
from kraken_client import get_closed_orders, get_open_orders, place_limit_order, place_take_profit_limit
from processed_orders import load_processed_orders, is_processed, save_processed_orders

ONE_MINUTE_AGO = int(time.time()) - 60
THREE_MONTHS_AGO = int(time.time()) - (60 * 60 * 24 * 7 * 4 * 3)
MAX_OPEN_SELLS = 4
MARGIN = 0.03 # 3%
LIMIT_BUFFER = 0.015 # 1.5%

def main():
    logging.info("Starting BoTC...")
    processed_orders = load_processed_orders()
    closed_orders = get_recently_closed_orders()
    if not closed_orders:
        logging.info("No closed orders returned.")
        return
    
    for order_id, order in closed_orders.items():
        if is_processed(order_id, processed_orders):
            logging.info(f"Order {order_id} already processed. Skipping...")
            continue
        process_order(order_id, order, processed_orders)
    
    save_processed_orders(processed_orders)

def get_recently_closed_orders(start=THREE_MONTHS_AGO, closed_after=ONE_MINUTE_AGO):
    try:
        response = get_closed_orders(start)
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

def process_order(order_id, order, processed_orders):
    logging.info(f"Processing order {order_id}...")
    side = order["descr"]["type"]
    price = float(order["price"])
    volume = float(order["vol_exec"])
    cost_eur = float(order["cost"])
    response = None

    if side == "buy":
        if not can_create_sell():
            logging.warning("Max open SELL orders reached. Skipping order creation.")
            return
        new_side = "sell"
        new_price = price * (1 + MARGIN)
        trigger_price = price * (1 + MARGIN) 
        limit_price = trigger_price * (1 - LIMIT_BUFFER)
        new_volume = volume
    elif side == "sell":
        new_side = "buy"
        new_price = price * (1 - MARGIN)
        trigger_price = price * (1 - MARGIN) 
        limit_price = trigger_price * (1 + LIMIT_BUFFER)
        new_volume = cost_eur / new_price

    try:
        response = place_take_profit_limit("XXBTZEUR", new_side, trigger_price, limit_price, new_volume)
        if "error" in response and response["error"]:
            raise Exception(response["error"])
        processed_orders.add(order_id)
        new_order = response['result']['txid'][0]
        logging.info(f"Created TP-LIMIT {new_side.upper()} order {new_order} | {new_volume:.8f} BTC @ trigger {trigger_price:,.1f}€ (limit {limit_price:,.1f}€)")
    except Exception as e:
        logging.error(f"Error creating {new_side.upper()} order: {e}")
    finally:
        if response:
            logging.info(f"API Response: {response}")

def can_create_sell():
    resp = get_open_orders()
    open_orders = resp.get("result", {}).get("open", {})
    sell_orders = [
        o for o in open_orders.values() 
        if o["descr"]["type"] == "sell" and o["descr"]["pair"] == "XBTEUR"
    ]
    return len(sell_orders) < MAX_OPEN_SELLS

if __name__ == "__main__":
    main()
