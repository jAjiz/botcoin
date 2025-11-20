import json
import os

os.makedirs("data", exist_ok=True)
STATE_FILE = "data/trailing_state.json"
CLOSED_ORDERS_FILE = "data/closed_orders.json"

def load_trailing_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_trailing_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def save_closed_order(order, order_id):
    if os.path.exists(CLOSED_ORDERS_FILE):
        with open(CLOSED_ORDERS_FILE, "r") as f:
            closed_orders = json.load(f)
        closed_orders[order_id] = order
        with open(CLOSED_ORDERS_FILE, "w") as fw:
            json.dump(closed_orders, fw, indent=2)
    else:
        with open(CLOSED_ORDERS_FILE, "w") as f:
            json.dump({order_id: order}, f, indent=2)

def is_processed(order_id, state):
    return order_id in state.keys()