from enum import StrEnum
from typing import Any

import core.logging as logging
from core import config
from core.config import ATR_DESV_LIMIT, MIN_VALUE, TRADING_PARAMS
from core.utils import new_cl_ord_id, now_utc, round_price
from exchange.kraken import (
    OrderState,
    OrderStatus,
    cancel_order,
    find_order_by_cl_ord_id,
    get_order_state,
    place_limit_order,
)
from trading.inventory_manager import calculate_position
from trading.parameters_manager import get_k_stop

# Never acted on: an order we cannot see is not known to be dead, so re-placing could leave two live exits.
UNRESOLVABLE_STATUSES = (OrderStatus.NOT_FOUND, OrderStatus.UNKNOWN)
# Only these give a definitive vol_exec: the order can never trade again.
TERMINAL_STATUSES = (OrderStatus.CLOSED, OrderStatus.CANCELED)


def _below_ordermin(pair: str, volume: float) -> bool:
    """True when Kraken would reject this size. Unknown ordermin never blocks a trade."""
    ordermin = config.PAIRS.get(pair, {}).get("ordermin")
    return ordermin is not None and volume < ordermin


class ClosingState(StrEnum):
    """The state ``manage_close_position`` leaves a closing position in."""

    FILLED = "filled"  # confirmed fill; pos carries the real closing_price and pnl_percent
    PENDING = "pending"  # an order rests at Kraken, or the position was dropped
    UNMANAGED = "unmanaged"  # latched with nothing resting at Kraken


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

    if _below_ordermin(pair, volume):
        logging.info(
            f"Cannot create {side.upper()} position: volume {volume:.8f} < Kraken ordermin "
            f"{config.PAIRS[pair]['ordermin']:.8f}"
        )
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

    if _below_ordermin(pair, volume):
        # A latched exit below ordermin can never be placed; the residual is untradeable by definition.
        _drop_position(f"volume {volume:.8f} < Kraken ordermin {config.PAIRS[pair]['ordermin']:.8f}")
        return False

    pos["volume"] = int(volume * 1e8) / 1e8
    return True


def is_open(pos: dict[str, Any] | None) -> bool:
    """A position is open only until its stop fires; ``stop_at`` is the single choke point."""
    return bool(pos) and not pos.get("stop_at")


def is_closing(pos: dict[str, Any] | None) -> bool:
    """A position whose stop has fired: the exact complement of ``is_open``."""
    return bool(pos) and bool(pos.get("stop_at"))


def _clear_closing_fields(pos: dict[str, Any]) -> None:
    """Clear a dead order's own fields; ``stop_at`` survives — the exit stays owed."""
    for key in ("closing_order_id", "closing_request_id", "closing_price"):
        pos.pop(key, None)


def finalize_close(pos: dict[str, Any], state: OrderState) -> bool:
    """Interpret an order already known to be terminal, writing the real ``closing_price``/``pnl_percent`` on a usable fill."""
    closing_order = pos.get("closing_order_id")
    # A cancel can race a complete fill. Measured against the order's own `vol`, never the drifting pos["volume"].
    fully_executed = state.vol > 0 and state.vol_exec >= state.vol
    if (state.status != OrderStatus.CLOSED and not fully_executed) or not state.avg_price or state.avg_price <= 0:
        return False
    if state.status != OrderStatus.CLOSED:
        logging.warning(
            f"Closing order {closing_order} ended as {state.status} but was fully executed "
            f"({state.vol_exec:.8f}); recording it as a completed close.",
            to_telegram=True,
        )
        pos["volume"] = state.vol_exec
    closing_price = state.avg_price
    entry = pos["entry_price"]
    side = pos["side"]
    pnl = (closing_price - entry) / entry * 100 if side == "sell" else (entry - closing_price) / entry * 100
    pos["closing_price"] = closing_price
    pos["pnl_percent"] = round(pnl, 4)
    logging.info(f"💸 Position closed: {pnl:+.2f}% result", to_telegram=True)
    return True


def _drive_closing_order(
    pair: str, pos: dict[str, Any], state: OrderState | None, last_prices: dict[str, float]
) -> ClosingState | None:
    """The single dispatch on a closing order's status; ``None`` means it is gone and cleared, so re-place this tick."""
    if state is None or state.status is OrderStatus.PENDING:
        return ClosingState.PENDING  # could not ask, or not on the book yet
    if state.status in UNRESOLVABLE_STATUSES:
        logging.error(
            f"[{pair}] Cannot resolve closing order {pos.get('closing_order_id')} (status={state.status}); "
            "leaving it untouched — re-placing blind could leave two live exits."
        )
        return ClosingState.UNMANAGED  # never guessed at
    if state.status is OrderStatus.OPEN:
        return ClosingState.PENDING if reprice_closing_order(pair, pos, state, last_prices) else ClosingState.UNMANAGED
    if finalize_close(pos, state):  # CLOSED or CANCELED
        return ClosingState.FILLED
    logging.warning(
        f"[{pair}] Closing order {pos.get('closing_order_id')} ended as {state.status} "
        "with no usable fill; re-placing the exit."
    )
    _clear_closing_fields(pos)
    return None


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
            pos["stop_at"] = now_utc()
            logging.info(
                f"[{pair}] ⛔ Stop price {round_price(pair, pos['stop_price']):,}€ hitted for {side.upper()} position.",
                to_telegram=True,
            )
            close_position(pair, pos, last_prices)
            return

        if (side == "sell" and current_price > pos["trailing_price"]) or (
            side == "buy" and current_price < pos["trailing_price"]
        ):
            update_stop_price(pair, pos, current_price, atr_val)
            logging.info(
                f"📈 Update {side.upper()} position: new trailing price {round_price(pair, pos['trailing_price']):,}€ | stop {round_price(pair, pos['stop_price']):,}€"
            )


def reprice_closing_order(pair: str, pos: dict[str, Any], state: OrderState, last_prices: dict[str, float]) -> bool:
    """Chase the fill of an order already known to be ``OPEN``; ``False`` only when it is off the book unreplaced."""
    order_id = pos["closing_order_id"]
    if state.vol_exec > 0:
        return True  # executing at its price; don't fragment the fill
    current_price = last_prices[pair]
    if round_price(pair, current_price) == round_price(pair, pos.get("closing_price")):
        return True  # identical limit; re-placing would only lose queue priority
    if not cancel_order(order_id):
        return True  # cancel unconfirmed: the order likely still rests, or filled

    # A fill can land during the cancel round trip: only a terminal status gives the definitive vol_exec to size from.
    post_cancel_state = get_order_state(order_id)
    if post_cancel_state is None or post_cancel_state.status not in TERMINAL_STATUSES:
        status = post_cancel_state.status if post_cancel_state else "unavailable"
        logging.warning(
            f"[{pair}] Could not confirm executed volume for canceled order {order_id} (status={status}); "
            "not placing a replacement. Next tick's terminal-status handling will resize the position."
        )
        return False  # confirmed cancel, no replacement: the pair is unmanaged

    side = pos["side"]
    # From the order's own numbers, never pos["volume"] (a lot tick apart after rounding); fails closed when `vol` is 0.0.
    remaining = post_cancel_state.vol - post_cancel_state.vol_exec
    if remaining <= 0:
        logging.info(
            f"[{pair}] Closing order {order_id} left no remainder after cancel "
            f"(vol_exec={post_cancel_state.vol_exec:.8f} of {post_cancel_state.vol:.8f}); "
            "not placing a replacement."
        )
        return True  # fully executed during the cancel window; branch 1 finalizes it next tick
    if post_cancel_state.vol_exec > 0:
        logging.warning(
            f"[{pair}] Closing order {order_id} partially filled during the cancel window: "
            f"{post_cancel_state.vol_exec:.8f} of {post_cancel_state.vol:.8f} executed; replacement sized to "
            f"{remaining:.8f}.",
            to_telegram=True,
        )

    # Written before the call (a lost response must still describe the attempt); the canceled txid is dropped, not kept.
    logging.info(
        f"[{pair}] 🔁 Repricing closing {side.upper()} order to {round_price(pair, current_price):,}€",
        to_telegram=True,
    )
    cl_ord_id = new_cl_ord_id()
    pos.update({"volume": remaining, "closing_price": current_price, "closing_request_id": cl_ord_id})
    pos.pop("closing_order_id", None)
    placed = place_limit_order(pair, side, current_price, remaining, cl_ord_id=cl_ord_id)
    if placed is None:
        logging.error(f"[{pair}] Closing order replacement not confirmed after cancel; the next tick will resolve it.")
        return False  # unconfirmed: manage_close_position's unconfirmed sub-state resolves it
    pos.update({"closing_order_id": placed.txid, "closing_price": placed.price, "volume": placed.volume})
    return True


def manage_close_position(
    pair: str,
    pos: dict[str, Any],
    balance: dict[str, Any],
    last_prices: dict[str, float],
    trailing_state: dict[str, Any],
) -> ClosingState:
    """Drive a latched position from the stop breach to the fill; ``FILLED`` reports it, never writes it."""
    if txid := pos.get("closing_order_id"):
        # Confirmed: Kraken gave us this id, so "not found" can only mean unmanaged.
        if (outcome := _drive_closing_order(pair, pos, get_order_state(txid), last_prices)) is not None:
            return outcome

    elif cl_ord_id := pos.get("closing_request_id"):
        # Unconfirmed: an AddOrder went out and we never learned what happened to it.
        found = find_order_by_cl_ord_id(cl_ord_id)
        if found is None:
            return ClosingState.UNMANAGED  # could not ask; decide nothing
        if found.txid:
            pos["closing_order_id"] = found.txid  # it landed after all
            logging.warning(f"[{pair}] Recovered closing order {found.txid} from its client id.")
            if (outcome := _drive_closing_order(pair, pos, found.state, last_prices)) is not None:
                return outcome
        else:
            _clear_closing_fields(pos)  # it never landed; stop_at survives

    # Nothing outstanding: a latched position never reaches tick_position, and a stale volume may be why the last attempt failed.
    if not refresh_position(pair, pos, balance, last_prices, trailing_state):
        return ClosingState.PENDING  # dropped: a resolved pair, not a failure

    placed = close_position(pair, pos, last_prices)
    return ClosingState.PENDING if placed else ClosingState.UNMANAGED


def close_position(pair: str, pos: dict[str, Any], last_prices: dict[str, float]) -> bool:
    """Place the exit order for an already-latched position; True only when one rests at Kraken."""
    try:
        side = pos["side"]
        current_price = last_prices[pair]
        volume = float(pos.get("volume", 0.0))

        cl_ord_id = new_cl_ord_id()
        pos.update({"closing_request_id": cl_ord_id, "closing_price": current_price})
        placed = place_limit_order(pair, side, current_price, volume, cl_ord_id=cl_ord_id)
        if placed is None:
            logging.error(f"[{pair}] Closing order not confirmed; it remains owed and will be resolved next tick.")
            return False
        pos.update({"closing_order_id": placed.txid, "closing_price": placed.price, "volume": placed.volume})

        # Announced on every attempt: the breach says the stop fired, this says an order rests.
        logging.info(
            f"[{pair}] 🏁 Placed closing {side.upper()} order at {round_price(pair, current_price):,}€ for {volume:.8f} vol",
            to_telegram=True,
        )
        return True
    except Exception as e:
        logging.error(f"Failed to close trailing position: {e}")
        return False
