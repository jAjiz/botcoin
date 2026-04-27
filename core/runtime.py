import threading

_lock = threading.Lock()
_shared_data = {
    "last_balance": {},
    "pairs_data": {},  # {pair: {"last_price": float, "atr": float}}
    "last_run_at": None,
}

def update_balance(balance):
    with _lock:
        _shared_data["last_balance"] = balance if balance else {}

def get_last_balance():
    with _lock:
        return dict(_shared_data["last_balance"])

def update_pair_data(pair, price=None, atr=None, volatility_level=None):
    with _lock:
        if pair not in _shared_data["pairs_data"]:
            _shared_data["pairs_data"][pair] = {}
        if price is not None:
            _shared_data["pairs_data"][pair]["last_price"] = price
        if atr is not None:
            _shared_data["pairs_data"][pair]["atr"] = atr
        if volatility_level is not None:
            _shared_data["pairs_data"][pair]["volatility_level"] = volatility_level

def get_pair_data(pair):
    with _lock:
        return dict(_shared_data["pairs_data"].get(pair, {}))

def update_last_run_at(last_run_at):
    with _lock:
        _shared_data["last_run_at"] = last_run_at

def get_last_run_at():
    with _lock:
        return _shared_data["last_run_at"]
