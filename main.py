import time
from config import logging, MARGIN, TRAILING_DISTANCE
from kraken_client import get_closed_orders, get_current_price, place_limit_order
from trailing_controller import load_trailing_state, save_trailing_state, is_processed

def main():
    try:
        while True:
            logging.info("🚀 [BoTC] ======== STARTING SESSION ========")
            trailing_state = load_trailing_state()

            one_minute_ago = int(time.time()) - 60
            one_day_Ago = int(time.time()) - (60 * 60 * 24 * 7)
            closed_orders = get_closed_orders(one_day_Ago, one_minute_ago)
            if closed_orders:
                for order_id, order in closed_orders.items():
                    if is_processed(order_id, trailing_state):
                        logging.info(f"Order {order_id} already processed. Skipping...")
                        continue
                    process_order(order_id, order, trailing_state)                
            else:
                logging.info("No closed orders returned.")

            update_trailing_orders(trailing_state)
        
            logging.info("Sleeping for 1 minute.")
            time.sleep(60)
    except KeyboardInterrupt:
        logging.info("🛑 Bot stopped manually by user.")

def process_order(order_id, order, trailing_state):
    logging.info(f"Processing order {order_id}...")
    volume = float(order["vol_exec"])
    cost = float(order["cost"])
    price = float(order["price"])
    side = order["descr"]["type"]
    pair = order["descr"]["pair"]
    trailing_price = price * (1 + MARGIN) if side == "buy" else price * (1 - MARGIN)

    if pair != "XBTEUR" or side not in ["buy", "sell"]:
        logging.info(f"Order {order_id} is not BTC/EUR or not a BUY/SELL order. Skipping...")
        return

    trailing_state[order_id] = {
        "side": side,
        "price": price,
        "volume": volume,
        "cost": cost,
        "trailing_active": False,
        "trailing_price": trailing_price,
        "stop_price": None,
    }

    logging.info(f"[CREATE] New TTP position created for {side.upper()} order {order_id}: Trailing price {trailing_price:,.1f}€")
    save_trailing_state(trailing_state)

def update_trailing_orders(trailing_state):
    current_price = get_current_price("XXBTZEUR")
    logging.info(f"Checking trailing positions with BTC/EUR price: {current_price:,.1f}€...")

    def activate_trailing():
        active = True
        new_trailing = current_price
        new_stop = current_price * (1 - TRAILING_DISTANCE) if side == "buy" else current_price * (1 + TRAILING_DISTANCE)
        logging.info(f"[ACTIVE] Trailing activated for order {order_id}: price at {new_trailing:,.1f}€ | stop at {new_stop:,.1f}€")
        return active, new_trailing, new_stop
    
    def update_stop_price():
        new_trailing = current_price
        new_stop = current_price * (1 - TRAILING_DISTANCE) if side == "buy" else current_price * (1 + TRAILING_DISTANCE)
        logging.info(f"[UPDATE] Order {order_id}: updated price {trailing_price:,.1f}€ --> {new_trailing:,.1f}€ | stop updated to {new_stop:,.1f}€")
        return new_trailing, new_stop

    def place_and_remove(new_side):
        try:
            logging.info(f"[CLOSE] Stop price {stop_price:,.1f}€ hit for order {order_id}: placing LIMIT {new_side.upper()} order")
            place_limit_order("XXBTZEUR", new_side, current_price, volume)

            if side == "buy":
                pnl = (current_price - entry_price) / entry_price * 100
            else:
                pnl = (entry_price - current_price) / entry_price * 100
            
            del trailing_state[order_id]
            logging.info(f"Trailing position for order {order_id} closed and removed.")
            logging.info(f"[PnL] Closed trade {order_id}: {pnl:+.2f}% gain before fees")

        except Exception as e:
            logging.error(f"Failed to place limit order for {order_id}: {e}")


    for order_id, pos in list(trailing_state.items()):
        side = pos["side"]
        entry_price = pos["price"]
        volume = pos["volume"]
        cost = pos["cost"]
        trailing_active = pos["trailing_active"]
        trailing_price = pos["trailing_price"]
        stop_price = pos["stop_price"]
        
        if side == "buy" :
            if not trailing_active and current_price >= trailing_price:
                pos["trailing_active"], pos["trailing_price"], pos["stop_price"] = activate_trailing()
            elif trailing_active:
                if current_price > trailing_price:
                    pos["trailing_price"], pos["stop_price"] = update_stop_price()
                if current_price <= stop_price:
                    place_and_remove("sell")

        elif side == "sell":
            if not trailing_active and current_price <= trailing_price:
                pos["trailing_active"], pos["trailing_price"], pos["stop_price"] = activate_trailing()
            elif trailing_active:
                if current_price < trailing_price:
                    pos["trailing_price"], pos["stop_price"] = update_stop_price()
                if current_price >= stop_price:
                    volume = cost / current_price
                    place_and_remove("buy")
    
    save_trailing_state(trailing_state)

if __name__ == "__main__":
    main()
