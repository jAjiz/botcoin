from typing import Any

import core.logging as logging
from core.config import ATR_DESV_LIMIT, MIN_VALUE, TRADING_PARAMS
from core.utils import now_utc
from exchange.kraken import get_order_status, place_limit_order
from trading.inventory_manager import calculate_position
from trading.parameters_manager import get_k_stop


def create_position(
    pair: str,
    balance: dict[str, Any],
    last_prices: dict[str, float],
    atr_val: float,
    trailing_state: dict[str, Any],
) -> None:
    current_price = last_prices[pair]
    side, value = calculate_position(pair, balance, last_prices, trailing_state)
    if value < MIN_VALUE:
        logging.info(f"Cannot create {side.upper()} position: value {value:.1f}€ < min {MIN_VALUE:.1f}€")
        return

    volume = value / current_price if current_price else 0.0
    if volume <= 0:
        logging.info(f"Cannot create {side.upper()} position: volume {volume:.8f} <= 0")
        return

    activation_price = calculate_activation_price(pair, side, current_price, atr_val)
    stored_volume = round(volume, 8)

    trailing_state[pair] = {
        "side": side,
        "volume": stored_volume,
        "entry_price": current_price,
        "activation_atr": round(atr_val, 1),
        "activation_price": round(activation_price, 1),
        "created_at": now_utc(),
    }

    logging.info(
        f"[{pair}] 🆕 New {side.upper()} position: {stored_volume:.8f} vol | {stored_volume * current_price:,.1f}€ cost | activation at {activation_price:,.1f}€",
        to_telegram=True,
    )


def calculate_activation_distance(pair: str, side: str, reference_price: float, atr_val: float) -> float:
    k_act = TRADING_PARAMS[pair][side]["K_ACT"]

    if k_act is not None:
        # Use K_ACT if defined, K_ACT = 0 means immediate activation
        return float(k_act) * atr_val

    # Use K_STOP and MIN_MARGIN if K_ACT is not defined
    k_stop = get_k_stop(pair, side, atr_val)
    min_margin = float(TRADING_PARAMS[pair][side]["MIN_MARGIN"])
    return k_stop * atr_val + min_margin * reference_price


def calculate_activation_price(pair: str, side: str, entry_price: float, atr_val: float) -> float:
    activation_distance = calculate_activation_distance(pair, side, entry_price, atr_val)
    activation_price = entry_price + activation_distance if side == "sell" else entry_price - activation_distance
    return activation_price


def update_activation_price(pair: str, pos: dict[str, Any], atr_val: float) -> None:
    side = pos["side"]
    entry_price = pos["entry_price"]
    activation_price = calculate_activation_price(pair, side, entry_price, atr_val)

    pos.update({"activation_price": round(activation_price, 1), "activation_atr": round(atr_val, 1)})


def reanchor_activation_price(pair: str, pos: dict[str, Any], current_price: float) -> bool:
    side = pos["side"]
    atr_val = pos["activation_atr"]
    expected_distance = calculate_activation_distance(pair, side, current_price, atr_val)
    gap = pos["activation_price"] - current_price if side == "sell" else current_price - pos["activation_price"]
    if gap <= expected_distance:
        return False

    pos["activation_price"] = round(calculate_activation_price(pair, side, current_price, atr_val), 1)
    return True


def calculate_stop_price(pair: str, side: str, trailing_price: float, atr_val: float) -> float:
    k_stop = get_k_stop(pair, side, atr_val)
    stop_distance = k_stop * atr_val

    stop_price = trailing_price - stop_distance if side == "sell" else trailing_price + stop_distance
    return stop_price


def update_stop_price(pair: str, pos: dict[str, Any], trailing_price: float, atr_val: float) -> None:
    side = pos["side"]
    stop_price = calculate_stop_price(pair, side, trailing_price, atr_val)

    pos.update({"trailing_price": trailing_price, "stop_price": round(stop_price, 1), "stop_atr": round(atr_val, 1)})


def refresh_position(
    pair: str,
    pos: dict[str, Any],
    balance: dict[str, Any],
    last_prices: dict[str, float],
    trailing_state: dict[str, Any],
) -> bool:
    side = pos["side"]
    current_price = last_prices[pair]

    def _drop_position(reason: str):
        logging.warning(f"Dropping {side.upper()} position: {reason}", to_telegram=True)
        trailing_state.pop(pair, None)

    _, value = calculate_position(pair, balance, last_prices, trailing_state, force_side=side)
    if value < MIN_VALUE:
        _drop_position(f"value {value:.1f}€ < minimum {MIN_VALUE:.1f}€")
        return False

    volume = value / current_price if current_price else 0.0
    if volume <= 0:
        _drop_position(f"volume {volume:.8f} <= 0")
        return False

    pos["volume"] = round(volume, 8)
    return True


def is_open(pos: dict[str, Any] | None) -> bool:
    return bool(pos) and not pos.get("closing_order_id")


def is_closing_complete(pos: dict[str, Any] | None) -> bool:
    if not pos:
        return False
    closing_order = pos.get("closing_order_id")
    if not closing_order:
        return False
    status = get_order_status(closing_order)
    return bool(status) and status not in ("pending", "open")


def tick_position(
    pair: str,
    pos: dict[str, Any],
    balance: dict[str, Any],
    last_prices: dict[str, float],
    atr_val: float,
    trailing_state: dict[str, Any],
) -> None:
    current_price = last_prices[pair]
    side = pos["side"]
    trailing_active = pos.get("trailing_price") is not None
    atr_limit_max = atr_val * (1 + ATR_DESV_LIMIT)
    atr_limit_min = atr_val * (1 - ATR_DESV_LIMIT)

    if not refresh_position(pair, pos, balance, last_prices, trailing_state):
        return

    if not trailing_active:
        if pos["activation_atr"] < atr_limit_min or pos["activation_atr"] > atr_limit_max:
            update_activation_price(pair, pos, atr_val)
            logging.info(f"♻️ Recalibrate {side.upper()} position: activation price to {pos['activation_price']:,}€.")

        if reanchor_activation_price(pair, pos, current_price):
            logging.info(f"🧭 Re-anchor {side.upper()} position: activation price to {pos['activation_price']:,}€.")

        if (side == "sell" and current_price >= pos["activation_price"]) or (
            side == "buy" and current_price <= pos["activation_price"]
        ):
            pos["activated_at"] = now_utc()
            logging.info(
                f"[{pair}] ⚡ Activation price {pos['activation_price']:,}€ reached for {side.upper()} position.",
                to_telegram=True,
            )
            update_stop_price(pair, pos, current_price, atr_val)
            logging.info(
                f"📈 Update {side.upper()} position: new trailing price {pos['trailing_price']:,}€ | stop {pos['stop_price']:,}€"
            )

    else:
        if pos["stop_atr"] < atr_limit_min or pos["stop_atr"] > atr_limit_max:
            update_stop_price(pair, pos, pos["trailing_price"], atr_val)
            logging.info(f"♻️ Recalibrate {side.upper()} position: stop price to {pos['stop_price']:,}€.")

        if (side == "sell" and current_price <= pos["stop_price"]) or (
            side == "buy" and current_price >= pos["stop_price"]
        ):
            close_position(pair, pos, last_prices)
            return

        if (side == "sell" and current_price > pos["trailing_price"]) or (
            side == "buy" and current_price < pos["trailing_price"]
        ):
            update_stop_price(pair, pos, current_price, atr_val)
            logging.info(
                f"📈 Update {side.upper()} position: new trailing price {pos['trailing_price']:,}€ | stop {pos['stop_price']:,}€"
            )


def close_position(pair: str, pos: dict[str, Any], last_prices: dict[str, float]) -> None:
    try:
        side = pos["side"]
        entry_price = pos["entry_price"]
        stop_price = pos["stop_price"]
        current_price = last_prices[pair]
        logging.info(
            f"[{pair}] ⛔ Stop price {stop_price:,}€ hitted: placing LIMIT {side.upper()} order", to_telegram=True
        )

        volume = float(pos.get("volume", 0.0))

        if side == "sell":
            pnl = (current_price - entry_price) / entry_price * 100
        else:
            pnl = (entry_price - current_price) / entry_price * 100

        closing_order = place_limit_order(pair, side, current_price, volume)
        if not closing_order:
            logging.error("Failed to place closing order. Aborting close.", to_telegram=True)
            return
        logging.info(f"💸 Closed position: {pnl:+.2f}% result", to_telegram=True)

        pos.update(
            {
                "volume": round(volume, 8),
                "closing_price": current_price,
                "closing_order_id": closing_order,
                "closing_requested_at": now_utc(),
                "pnl_percent": round(pnl, 4),
            }
        )
    except Exception as e:
        # Recoverable: scheduler must keep ticking; surface failure via Telegram.
        logging.error(f"Failed to close trailing position: {e}", to_telegram=True)
