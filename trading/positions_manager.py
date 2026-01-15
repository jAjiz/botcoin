import core.logging as logging
from trading.inventory_manager import calculate_position
from trading.parameters_manager import get_k_stop
from core.utils import now_str
from core.config import TRADING_PARAMS, MIN_VALUE
from core.state import load_closed_positions
from exchange.kraken import place_limit_order

def create_position(pair, balance, last_prices, atr_val, trailing_state):
    current_price = last_prices[pair]
    side, value = calculate_position(pair, balance, last_prices, trailing_state)
    if value < MIN_VALUE:
        logging.info(f"Cannot create {side.upper()} position: value {value:.1f}€ < min {MIN_VALUE:.1f}€")
        return
    
    volume = value / current_price if current_price else 0.0
    if volume <= 0:
        logging.warning(f"Cannot create {side.upper()} position: volume {volume:.8f} <= 0")
        return
    
    # Get entry_price from last closed position with opposite side
    entry_price = current_price
    closed_positions = load_closed_positions()
    if pair in closed_positions and closed_positions[pair]:
        for pos in reversed(closed_positions[pair]):
            if pos.get("side") != side:
                entry_price = pos.get("closing_price", current_price)
                break

    activation_price = calculate_activation_price(pair, side, entry_price, atr_val)

    trailing_state[pair] = {
        "side": side,
        "volume": round(volume, 8),
        "entry_price": entry_price,
        "activation_atr": round(atr_val, 1),
        "activation_price": round(activation_price, 1),
        "creation_time": now_str()
    }
    
    logging.info(f"[{pair}] 🆕 New {side.upper()} position: activation at {activation_price:,.1f}€",
                  to_telegram=True)  

def calculate_activation_price(pair, side, entry_price, atr_val):
    k_act = TRADING_PARAMS[pair][side]["K_ACT"]

    if k_act is not None:
        # Use K_ACT if defined, K_ACT = 0 means immediate activation
        activation_distance = float(k_act) * atr_val
    else:
        # Use K_STOP and MIN_MARGIN if K_ACT is not defined
        k_stop = get_k_stop(pair, side, atr_val)
        min_margin = float(TRADING_PARAMS[pair][side]["MIN_MARGIN"])
        activation_distance = k_stop * atr_val + min_margin * entry_price

    if side == "sell":
        activation_price = entry_price - activation_distance
    else:
        activation_price = entry_price + activation_distance

    return activation_price

def update_activation_price(pair, pos, atr_val):
    side = pos["side"]
    entry_price = pos["entry_price"]
    activation_price = calculate_activation_price(pair, side, entry_price, atr_val)

    pos.update({
        "activation_price": round(activation_price, 1),
        "activation_atr": round(atr_val, 1)
    })

def calculate_stop_price(pair, side, trailing_price, atr_val):
    k_stop = get_k_stop(pair, side, atr_val)
    stop_distance = k_stop * atr_val

    if side == "sell":
        stop_price = trailing_price - stop_distance
    else:
        stop_price = trailing_price + stop_distance

    return stop_price

def update_stop_price(pair, pos, trailing_price, atr_val):
    side = pos["side"]
    stop_price = calculate_stop_price(pair, side, trailing_price, atr_val)

    pos.update({
        "trailing_price": trailing_price,
        "stop_price": round(stop_price, 1),
        "stop_atr": round(atr_val, 1)
    })

def close_position(pair, pos, balance, last_prices, trailing_state):
    try:
        side = pos["side"]
        entry_price = pos["entry_price"]
        stop_price = pos["stop_price"]
        current_price = last_prices[pair]
        logging.info(f"[{pair}] ⛔ Stop price {stop_price:,}€ hitted: placing LIMIT {side.upper()} order",
                        to_telegram=True)
        
        def _drop_position(reason: str):
            logging.warning(f"Dropping {side.upper()} position: {reason}", to_telegram=True)
            if pair in trailing_state:
                del trailing_state[pair]

        _, value = calculate_position(pair, balance, last_prices, trailing_state, force_side=side)
        if value < MIN_VALUE:
            _drop_position(f"value {value:.1f}€ < minimum {MIN_VALUE:.1f}€")
            return

        volume = value / current_price if current_price else 0.0
        if volume <= 0:
            _drop_position(f"volume {volume:.8f} <= 0")
            return

        if side == "sell":
            pnl = (current_price - entry_price) / entry_price * 100
        else:
            pnl = (entry_price - current_price) / entry_price * 100

        closing_order = place_limit_order(pair, side, current_price, volume)
        if not closing_order:
            logging.error(f"Failed to place closing order. Aborting close.", to_telegram=True)
            return
        logging.info(f"💸 Closed position: {pnl:+.2f}% result", to_telegram=True)

        pos.update({
            "volume": round(volume, 8),
            "closing_price": current_price,
            "closing_order": closing_order,
            "closing_time": now_str(),
            "pnl": round(pnl, 2)
        })
    except Exception as e:
        logging.error(f"Failed to close trailing position: {e}", to_telegram=True)