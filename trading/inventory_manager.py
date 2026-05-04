from typing import Any

from core.config import ASSET_ALLOCATION, FIAT_CODE, PAIRS


def get_fiat_balance(balance: dict[str, Any]) -> float:
    return float(balance.get(FIAT_CODE, 0.0))


def get_portfolio_value(balance: dict[str, Any], last_prices: dict[str, float]) -> float:
    total_value = 0.0

    # Convert crypto assets with last prices
    for pair in PAIRS:
        asset = PAIRS[pair]["base"]

        amount = float(balance.get(asset, 0.0))
        if amount > 0:
            raw_price = last_prices.get(pair)
            if raw_price is None:
                continue
            price = float(raw_price)
            asset_value = amount * price
            total_value += asset_value

    # Add fiat balance
    total_value += get_fiat_balance(balance)

    return total_value


def get_available_fiat(balance: dict[str, Any], last_prices: dict[str, float], trailing_state: dict[str, Any]) -> float:
    fiat_balance = get_fiat_balance(balance)

    reserved_fiat = 0.0
    for pair, pos in trailing_state.items():
        if not pos or pos.get("side") != "buy":
            continue
        volume = float(pos.get("volume", 0.0))
        raw_price = last_prices.get(pair)
        if raw_price is None:
            continue
        price = float(raw_price)
        if volume > 0 and price > 0:
            reserved_fiat += volume * price

    available_fiat = fiat_balance - reserved_fiat
    return available_fiat if available_fiat > 0 else 0.0


def get_base_value(pair: str, balance: dict[str, Any], current_price: float) -> float:
    asset = PAIRS[pair]["base"]
    amount = float(balance.get(asset, 0.0))
    if current_price is not None:
        base_value = amount * current_price
        return base_value
    return 0.0


def get_target_value(pair: str, portfolio_value: float) -> float:
    target_pct = float(ASSET_ALLOCATION[pair].get("TARGET_PCT", 0))
    target_value = (target_pct / 100.0) * portfolio_value
    return target_value


def get_hodl_value(pair: str, target_value: float) -> float:
    hodl_pct = float(ASSET_ALLOCATION[pair].get("HODL_PCT", 0))
    hodl_value = (hodl_pct / 100.0) * target_value
    return hodl_value


def calculate_pair_values(
    pair: str, balance: dict[str, Any], last_prices: dict[str, float]
) -> tuple[float, float, float]:
    portfolio_value = get_portfolio_value(balance, last_prices)
    target_value = get_target_value(pair, portfolio_value)
    current_price = last_prices[pair]
    current_value = get_base_value(pair, balance, current_price)
    hodl_value = get_hodl_value(pair, target_value)

    return target_value, current_value, hodl_value


def calculate_position(
    pair: str,
    balance: dict[str, Any],
    last_prices: dict[str, float],
    trailing_state: dict[str, Any],
    force_side: str | None = None,
) -> tuple[str, float]:
    target_value, current_value, hodl_value = calculate_pair_values(pair, balance, last_prices)

    # Exclude self from trailing state to avoid double counting reserved fiat
    ts_excluding_self = dict(trailing_state or {})
    ts_excluding_self.pop(pair, None)

    # Sell value is amount above hodl, buy value is amount needed to reach target
    sell_value = max(0.0, float(current_value) - float(hodl_value))
    buy_value = max(
        0.0,
        min(
            max(0.0, float(target_value) - float(current_value)),
            max(0.0, float(get_available_fiat(balance, last_prices, ts_excluding_self))),
        ),
    )

    if force_side in ("buy", "sell"):
        return ("buy", buy_value) if force_side == "buy" else ("sell", sell_value)

    return ("buy", buy_value) if buy_value > sell_value else ("sell", sell_value)
