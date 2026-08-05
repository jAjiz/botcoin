from typing import Any

import core.logging as logging
from core.config import ATR_DESV_LIMIT, MIN_VALUE, TRADING_PARAMS
from core.utils import now_utc, round_price
from exchange.kraken import cancel_order, get_order_state, place_limit_order
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
    stored_volume = int(volume * 1e8) / 1e8

    trailing_state[pair] = {
        "side": side,
        "volume": stored_volume,
        "entry_price": current_price,
        "activation_atr": atr_val,
        "activation_price": activation_price,
        "created_at": now_utc(),
    }

    logging.info(
        f"[{pair}] 🆕 New {side.upper()} position: {stored_volume:.8f} vol | {stored_volume * current_price:,.2f}€ cost | activation at {round_price(pair, activation_price):,}€",
        to_telegram=True,
    )


def calculate_activation_distance(pair: str, side: str, reference_price: float, atr_val: float) -> float:
    k_act = TRADING_PARAMS[pair]["K_ACT"]

    if k_act is not None:
        return float(k_act) * atr_val  # K_ACT = 0 means immediate activation

    k_stop = get_k_stop(pair, side, atr_val)
    min_margin = float(TRADING_PARAMS[pair]["MIN_MARGIN"])
    return k_stop * atr_val + min_margin * reference_price


def calculate_activation_price(pair: str, side: str, entry_price: float, atr_val: float) -> float:
    activation_distance = calculate_activation_distance(pair, side, entry_price, atr_val)
    activation_price = entry_price + activation_distance if side == "sell" else entry_price - activation_distance
    return activation_price


def update_activation_price(pair: str, pos: dict[str, Any], atr_val: float) -> None:
    side = pos["side"]
    entry_price = pos["entry_price"]
    activation_price = calculate_activation_price(pair, side, entry_price, atr_val)

    pos.update({"activation_price": activation_price, "activation_atr": atr_val})


def reanchor_activation_price(pair: str, pos: dict[str, Any], current_price: float) -> bool:
    side = pos["side"]
    atr_val = pos["activation_atr"]
    expected_distance = calculate_activation_distance(pair, side, current_price, atr_val)
    gap = pos["activation_price"] - current_price if side == "sell" else current_price - pos["activation_price"]
    if gap <= expected_distance:
        return False

    pos["activation_price"] = calculate_activation_price(pair, side, current_price, atr_val)
    return True


def calculate_stop_price(pair: str, side: str, trailing_price: float, atr_val: float) -> float:
    k_stop = get_k_stop(pair, side, atr_val)
    stop_distance = k_stop * atr_val

    stop_price = trailing_price - stop_distance if side == "sell" else trailing_price + stop_distance
    return stop_price


def update_stop_price(pair: str, pos: dict[str, Any], trailing_price: float, atr_val: float) -> None:
    side = pos["side"]
    stop_price = calculate_stop_price(pair, side, trailing_price, atr_val)

    pos.update({"trailing_price": trailing_price, "stop_price": stop_price, "stop_atr": atr_val})


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

    pos["volume"] = int(volume * 1e8) / 1e8
    return True


def is_open(pos: dict[str, Any] | None) -> bool:
    return bool(pos) and not pos.get("closing_order_id")


def is_closing_complete(pos: dict[str, Any] | None) -> bool:
    """Check if the closing order is filled. If so, update pos with the real fill
    price and PnL. Any terminal outcome that cannot be finalized instead clears the
    closing fields: leaving them set would freeze the position forever, since the
    status can never change again and ``is_open`` stays False."""
    if not pos:
        return False
    closing_order = pos.get("closing_order_id")
    if not closing_order:
        return False
    state = get_order_state(closing_order)
    if state is None or state.status in ("pending", "open"):
        return False
    if state.status != "closed" or not state.avg_price or state.avg_price <= 0:
        logging.warning(
            f"Closing order {closing_order} ended as {state.status} with no usable fill price; "
            "resuming position management.",
            to_telegram=True,
        )
        for key in ("closing_order_id", "closing_price", "closing_requested_at"):
            pos.pop(key, None)
        return False
    closing_price = state.avg_price
    entry = pos["entry_price"]
    side = pos["side"]
    pnl = (closing_price - entry) / entry * 100 if side == "sell" else (entry - closing_price) / entry * 100
    pos["closing_price"] = closing_price
    pos["pnl_percent"] = round(pnl, 4)
    logging.info(f"💸 Position closed: {pnl:+.2f}% result", to_telegram=True)
    return True


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
            logging.info(
                f"♻️ Recalibrate {side.upper()} position: activation price to {round_price(pair, pos['activation_price']):,}€."
            )

        if reanchor_activation_price(pair, pos, current_price):
            logging.info(
                f"🧭 Re-anchor {side.upper()} position: activation price to {round_price(pair, pos['activation_price']):,}€."
            )

        if (side == "sell" and current_price >= pos["activation_price"]) or (
            side == "buy" and current_price <= pos["activation_price"]
        ):
            pos["activated_at"] = now_utc()
            logging.info(
                f"[{pair}] ⚡ Activation price {round_price(pair, pos['activation_price']):,}€ reached for {side.upper()} position.",
                to_telegram=True,
            )
            update_stop_price(pair, pos, current_price, atr_val)
            logging.info(
                f"📈 Update {side.upper()} position: new trailing price {round_price(pair, pos['trailing_price']):,}€ | stop {round_price(pair, pos['stop_price']):,}€"
            )

    else:
        if pos["stop_atr"] < atr_limit_min or pos["stop_atr"] > atr_limit_max:
            update_stop_price(pair, pos, pos["trailing_price"], atr_val)
            logging.info(
                f"♻️ Recalibrate {side.upper()} position: stop price to {round_price(pair, pos['stop_price']):,}€."
            )

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
                f"📈 Update {side.upper()} position: new trailing price {round_price(pair, pos['trailing_price']):,}€ | stop {round_price(pair, pos['stop_price']):,}€"
            )


def reprice_closing_order(pair: str, pos: dict[str, Any], last_prices: dict[str, float]) -> None:
    """Chase the fill of a still-open closing order: cancel it and re-place the
    limit at the current market price."""
    order_id = pos.get("closing_order_id")
    if not order_id:
        return
    state = get_order_state(order_id)
    if state is None or state.status != "open":
        # A pending order isn't on the book yet, so cancel/replace is pure churn;
        # terminal states are is_closing_complete's job.
        return
    if state.vol_exec > 0:
        return  # executing at its price; don't fragment the fill
    current_price = last_prices[pair]
    if round_price(pair, current_price) == round_price(pair, pos.get("closing_price")):
        return  # identical limit; re-placing would only lose queue priority
    if not cancel_order(order_id):
        return  # likely filled in the race window; next tick resolves it

    # A fill can land between the vol_exec check above and the cancel call just
    # completed: the cancel still succeeds (a cancellable remainder existed) but
    # some volume executed in between. Re-query for the definitive vol_exec of
    # the now-canceled order so the replacement is sized at the remainder, not
    # the full position (over-selling by the executed amount otherwise).
    post_cancel_state = get_order_state(order_id)
    if post_cancel_state is None:
        logging.warning(
            f"[{pair}] Could not confirm executed volume for canceled order {order_id}; "
            "not placing a replacement. Next tick's terminal-status handling will resize the position.",
            to_telegram=True,
        )
        return

    side = pos["side"]
    volume = float(pos.get("volume", 0.0))
    remaining = volume - post_cancel_state.vol_exec
    if remaining <= 0:
        logging.info(
            f"[{pair}] Closing order {order_id} left no remainder after cancel "
            f"(vol_exec={post_cancel_state.vol_exec:.8f} of {volume:.8f}); not placing a replacement."
        )
        return
    if post_cancel_state.vol_exec > 0:
        logging.info(
            f"[{pair}] Closing order {order_id} partially filled ({post_cancel_state.vol_exec:.8f}) "
            "during the cancel window; replacement sized to the remainder."
        )

    new_order = place_limit_order(pair, side, current_price, remaining)
    if not new_order:
        logging.error("Failed to re-place closing order after cancel.", to_telegram=True)
        return
    pos["volume"] = remaining
    pos.update(
        {
            "closing_price": current_price,
            "closing_order_id": new_order,
        }
    )
    logging.info(
        f"[{pair}] 🔁 Repriced closing {side.upper()} order to {round_price(pair, current_price):,}€",
        to_telegram=True,
    )


def close_position(pair: str, pos: dict[str, Any], last_prices: dict[str, float]) -> None:
    try:
        side = pos["side"]
        stop_price = pos["stop_price"]
        current_price = last_prices[pair]
        volume = float(pos.get("volume", 0.0))
        logging.info(
            f"[{pair}] ⛔ Stop price {round_price(pair, stop_price):,}€ hitted: placing LIMIT {side.upper()} order | {volume:.8f} @ {round_price(pair, current_price):,}€",
            to_telegram=True,
        )

        closing_order = place_limit_order(pair, side, current_price, volume)
        if not closing_order:
            logging.error("Failed to place closing order. Aborting close.", to_telegram=True)
            return

        pos.update(
            {
                "volume": round(volume, 8),
                "closing_price": current_price,
                "closing_order_id": closing_order,
                "closing_requested_at": now_utc(),
            }
        )
    except Exception as e:
        # Recoverable: scheduler must keep ticking; surface failure via Telegram.
        logging.error(f"Failed to close trailing position: {e}", to_telegram=True)
