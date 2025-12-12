import json
import os

os.makedirs("data", exist_ok=True)
STATE_FILE = "data/trailing_state.json"
CLOSED_FILE = "data/closed_positions.json"

def load_trailing_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_trailing_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def load_closed_positions():
    if os.path.exists(CLOSED_FILE):
        with open(CLOSED_FILE, "r") as f:
            return json.load(f)
    return {}

def save_closed_position(pos, order_id, pair):
    if os.path.exists(CLOSED_FILE):
        with open(CLOSED_FILE, "r") as f:
            closed_positions = json.load(f)
    else:
        closed_positions = {}
    
    if pair not in closed_positions:
        closed_positions[pair] = {}
    
    closed_positions[pair][order_id] = pos
    
    with open(CLOSED_FILE, "w") as f:
        json.dump(closed_positions, f, indent=2)

def is_processed(order_id, state):
    for pos in state.values():
        opening_orders = pos.get("opening_order")
        if opening_orders and order_id in opening_orders:
            return True
    return False