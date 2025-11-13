import json
import os

os.makedirs("data", exist_ok=True)
STATE_FILE = "data/trailing_state.json"

def load_trailing_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_trailing_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def is_processed(order_id, state):
    return order_id in state.keys()