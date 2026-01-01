import threading

# Thread-safe shared data between main thread and Telegram thread
_lock = threading.Lock()
_shared_data = {
    "last_balance": {},
    "pairs_data": {},  # {pair: {"last_price": float, "atr": float}}
    "trailing_state": {}  # {pair: {order_id: position_data}}
}

def update_balance(balance):
    with _lock:
        _shared_data["last_balance"] = balance if balance else {}

def get_last_balance():
    with _lock:
        return _shared_data["last_balance"]

def update_pair_data(pair, price=None, atr=None):
    with _lock:
        if pair not in _shared_data["pairs_data"]:
            _shared_data["pairs_data"][pair] = {}
        if price is not None:
            _shared_data["pairs_data"][pair]["last_price"] = price
        if atr is not None:
            _shared_data["pairs_data"][pair]["atr"] = atr

def get_pair_data(pair):
    with _lock:
        return _shared_data["pairs_data"].get(pair, {})

def update_trailing_state(trailing_state):
    with _lock:
        _shared_data["trailing_state"] = trailing_state if trailing_state else {}

def get_trailing_state():
    with _lock:
        # Return a deep copy to avoid modifications from the reading thread
        import copy
        return copy.deepcopy(_shared_data["trailing_state"])
