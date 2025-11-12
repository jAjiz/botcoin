import time
from config import logging, MARGIN, LIMIT_BUFFER, MAX_OPEN_SELLS
from kraken_client import get_closed_orders, get_open_orders, get_current_price, place_take_profit_limit, cancel_order
from processed_orders import load_processed_orders, is_processed, save_processed_orders

ONE_MINUTE_AGO = int(time.time()) - 60
THREE_MONTHS_AGO = int(time.time()) - (60 * 60 * 24 * 7 * 4 * 3)

def main():
    try:
        while True:
            logging.info("🚀 [BoTC] ======== STARTING SESSION ========")
            processed_orders = load_processed_orders()
            open_orders = get_open_orders()

            closed_orders = get_closed_orders(start=THREE_MONTHS_AGO, closed_after=ONE_MINUTE_AGO)
            if closed_orders:
                for order_id, order in closed_orders.items():
                    if is_processed(order_id, processed_orders):
                        logging.info(f"Order {order_id} already processed. Skipping...")
                        continue
                    process_order(order_id, order, processed_orders, open_orders)
                
                save_processed_orders(processed_orders)
            else:
                logging.info("No closed orders returned.")

            if open_orders:
                update_open_orders(open_orders)
        
            logging.info("Sleeping for 1 minute.")
            time.sleep(60)
    except KeyboardInterrupt:
        logging.info("🛑 Bot stopped manually by user.")

def process_order(order_id, order, processed_orders, open_orders):
    logging.info(f"Processing order {order_id}...")
    volume = float(order["vol_exec"])
    cost_eur = float(order["cost"])
    price = float(order["price"])
    side = order["descr"]["type"]
    response = None

    if side == "buy":
        if not can_create_sell(open_orders):
            logging.warning("Max open SELL orders reached. Skipping order creation.")
            return
        new_side = "sell"
    elif side == "sell":
        new_side = "buy"

    trigger_price, limit_price, new_volume = calculate_trading_params(new_side, price, volume, cost_eur)
    
    try:
        response = place_take_profit_limit("XXBTZEUR", new_side, trigger_price, limit_price, new_volume)
        if "error" in response and response["error"]:
            raise Exception(response["error"])
        processed_orders.add(order_id)
        new_order = response.get('result', {}).get('txid', [None])[0]
        logging.info(f"Created TP-LIMIT {new_side.upper()} order {new_order} | {new_volume:.8f} BTC @ trigger {trigger_price:,.1f}€ (limit {limit_price:,.1f}€)")
    except Exception as e:
        logging.error(f"Error creating {new_side.upper()} order: {e}")
    finally:
        if response:
            logging.info(f"API Response: {response}")

def can_create_sell(open_orders):
    sell_orders = [
        o for o in open_orders.values() 
        if o["descr"]["type"] == "sell" and o["descr"]["pair"] == "XBTEUR"
    ]
    return len(sell_orders) < MAX_OPEN_SELLS

def calculate_trading_params(side, price, volume, cost_eur):
    if side == "buy":
        new_trigger = price * (1 - MARGIN)
        new_limit = new_trigger * (1 + LIMIT_BUFFER)
        new_volume = cost_eur / new_trigger
    elif side == "sell":
        new_trigger = price * (1 + MARGIN)
        new_limit = new_trigger * (1 - LIMIT_BUFFER)
        new_volume = volume
    return new_trigger, new_limit, new_volume

def update_open_orders(open_orders):
    try:
        current_price = get_current_price("XXBTZEUR")
        logging.info(f"Checking TP-LIMIT orders with BTC/EUR price: {current_price:,.1f}€")

        for order_id, order in open_orders.items():
            volume = float(order["vol"])
            cost_eur = float(order["cost"])
            side = order["descr"]["type"]
            pair = order["descr"]["pair"]
            order_type = order["descr"]["ordertype"]
            trigger_price = float(order["descr"]["price"])
            update_order = False

            # Only update TP-LIMIT orders for BTC/EUR
            if pair == "XBTEUR" and order_type == "take-profit-limit":
                new_trigger, new_limit, new_volume = calculate_trading_params(side, current_price, volume, cost_eur)

                if side == "sell" and (new_trigger > trigger_price): update_order = True
                elif side == "buy" and (new_trigger < trigger_price): update_order = True

                if update_order:
                    logging.info(f"Updating {side.upper()} order {order_id}: trigger {trigger_price:,.1f} → {new_trigger:,.1f}")
                    try:
                        # Cancel old order
                        cancel_resp = cancel_order(order_id)
                        if "error" in cancel_resp and cancel_resp["error"]:
                            raise Exception(cancel_resp["error"])

                        # Create new updated order
                        new_order_resp = place_take_profit_limit("XXBTZEUR", side, new_trigger, new_limit, new_volume)
                        if "error" in new_order_resp and new_order_resp["error"]:
                            raise Exception(new_order_resp["error"])
                        new_order = new_order_resp.get('result', {}).get('txid', [None])[0]
                        logging.info(f"Created TP-LIMIT {side.upper()} order {new_order} | {new_volume:.8f} BTC @ trigger {new_trigger:,.1f}€ (limit {new_limit:,.1f}€)")
                    except Exception as e:
                        logging.error(f"Error updating order {order_id}: {e}")
    except Exception as e:
        logging.error(f"Error while updating open orders: {e}")

if __name__ == "__main__":
    main()
