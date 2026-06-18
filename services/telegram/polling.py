import asyncio
import logging
from typing import Any

from core.config import FIAT_CODE, PAIRS, TELEGRAM_TOKEN, TELEGRAM_USER_ID
from services.telegram.client import client
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.bot").setLevel(logging.WARNING)

_FIELD_MAP = {
    "target": "target_pct",
    "hodl": "hodl_pct",
    "kact": "k_act",
    "margin": "min_margin",
    "stop-ll": "stop_pct_ll",
    "stop-lv": "stop_pct_lv",
    "stop-mv": "stop_pct_mv",
    "stop-hv": "stop_pct_hv",
    "stop-hh": "stop_pct_hh",
}
_CONFIG_FIELDS = tuple(_FIELD_MAP)


def _format_pair_config(item: dict) -> str:
    k_act = item.get("k_act")
    k_act_str = "None" if k_act is None else f"{k_act:g}"
    return (
        f"━━━ {item['pair']} ━━━\n"
        f"Target %: {item['target_pct']:g}  |  Hodl %: {item['hodl_pct']:g}\n"
        f"K-act: {k_act_str}  |  Min margin: {item['min_margin']:g}\n"
        f"Stop percentiles: LL {item['stop_pct_ll']:g} -  LV {item['stop_pct_lv']:g}  -  MV {item['stop_pct_mv']:g}\n"
        f"HV {item['stop_pct_hv']:g}  -  HH {item['stop_pct_hh']:g}"
    )


def _check_auth(update: Update) -> bool:
    if not TELEGRAM_USER_ID:
        return False
    return update.effective_user.id == int(TELEGRAM_USER_ID)


def _pnl_percent(pos: dict[str, Any], last_price: float) -> float | None:
    trailing_price = pos.get("trailing_price")
    stop_price = pos.get("stop_price")
    if trailing_price is None or stop_price is None:
        return None
    entry_price = pos["entry_price"]
    if pos["side"] == "sell":
        return (stop_price - entry_price) / entry_price * 100
    return (entry_price - stop_price) / entry_price * 100


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    pairs_list = ", ".join(PAIRS.keys())
    await update.message.reply_text(
        "📋 Available commands:\n\n"
        "/status - Bot status and configured pairs\n"
        "/pause - Pause bot operations\n"
        "/resume - Resume bot operations\n"
        "/market [pair] - Current market data (all or specific pair)\n"
        "/positions [pair] - Open positions (all or specific pair)\n"
        "/config [pair] - Show pair configuration (all or specific pair)\n"
        "/setconfig <pair> <field> <value> - Update a config field\n"
        "/help - Show this help\n\n"
        f"Configured pairs: {pairs_list}\n"
        "Example: /market XBTEUR"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    try:
        resp = await client.get("/status")
        resp.raise_for_status()
        data = resp.json()
        status = "⏸ PAUSED" if data["paused"] else "▶️ RUNNING"
        last_run_at = data.get("last_run_at") or "N/A"
        await update.message.reply_text(f"Status: {status}\nLast run: {last_run_at}")
    except Exception as e:
        logging.error(f"Error in status_command: {e}")
        await update.message.reply_text("❌ Could not fetch bot status.")


async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    try:
        resp = await client.post("/control/pause", json={"updated_by": "telegram"})
        resp.raise_for_status()
        await update.message.reply_text("⏸ BoTC paused. New operations will not be processed.")
    except Exception as e:
        logging.error(f"Error in pause_command: {e}")
        await update.message.reply_text("❌ Could not pause the bot.")


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    try:
        resp = await client.post("/control/resume", json={"updated_by": "telegram"})
        resp.raise_for_status()
        await update.message.reply_text("▶️ BoTC resumed.")
    except Exception as e:
        logging.error(f"Error in resume_command: {e}")
        await update.message.reply_text("❌ Could not resume the bot.")


async def market_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    try:
        pair_filter = context.args[0].upper() if context.args else None
        if pair_filter and pair_filter not in PAIRS:
            await update.message.reply_text(f"❌ Unknown pair: {pair_filter}\nAvailable: {', '.join(PAIRS.keys())}")
            return

        market_url = f"/market/{pair_filter}" if pair_filter else "/market"
        market_resp, balance_resp = await asyncio.gather(
            client.get(market_url),
            client.get("/balance"),
        )
        market_resp.raise_for_status()
        balance_resp.raise_for_status()

        market_items = market_resp.json()
        if pair_filter:
            market_items = [market_items]
        balance = balance_resp.json()["balance"]
        market_by_pair = {item["pair"]: item for item in market_items}

        msg = "📈 Market Status:\n\n"
        for pair in [pair_filter] if pair_filter else list(PAIRS.keys()):
            item = market_by_pair.get(pair, {})
            price = item.get("last_price")
            atr = item.get("atr")
            vol = item.get("volatility_level", "N/A")
            asset = item.get("base_asset")
            asset_balance = float(balance.get(asset, 0))
            asset_value_eur = asset_balance * price if price else 0
            msg += (
                f"━━━ {pair} ━━━\n"
                f"Price: {price:,.2f}€\n"
                f"ATR: {atr:,.2f}€ ({vol})\n"
                f"Balance: {asset_balance:.8f} ({asset_value_eur:,.2f}€)\n\n"
            )

        fiat_balance = float(balance.get(FIAT_CODE, 0.0))
        msg += f"{FIAT_CODE} Balance: {fiat_balance:,.2f}€"
        await update.message.reply_text(msg)
    except Exception as e:
        logging.error(f"Error in market_command: {e}")
        await update.message.reply_text(f"❌ Error fetching market status: {e}")


async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    try:
        pair_filter = context.args[0].upper() if context.args else None
        if pair_filter and pair_filter not in PAIRS:
            await update.message.reply_text(f"❌ Unknown pair: {pair_filter}\nAvailable: {', '.join(PAIRS.keys())}")
            return

        positions_url = f"/positions/{pair_filter}" if pair_filter else "/positions"
        market_url = f"/market/{pair_filter}" if pair_filter else "/market"
        positions_resp, market_resp = await asyncio.gather(
            client.get(positions_url),
            client.get(market_url),
        )
        positions_resp.raise_for_status()
        market_resp.raise_for_status()

        positions_data = positions_resp.json()
        market_items = market_resp.json()

        if pair_filter:
            pos_by_pair = {positions_data["pair"]: positions_data.get("position")}
            market_items = [market_items]
        else:
            pos_by_pair = positions_data

        price_by_pair = {item["pair"]: item.get("last_price", 0) for item in market_items}
        pairs_to_show = [pair_filter] if pair_filter else list(PAIRS.keys())

        msg = "📊 Open Positions:\n\n"
        for pair in pairs_to_show:
            last_price = price_by_pair.get(pair, 0)
            msg += f"━━━ {pair} (Last price: {last_price:,.2f}€) ━━━\n"

            pos = pos_by_pair.get(pair)
            if not pos:
                msg += "⚠️ No open position for this pair.\n\n"
                continue

            trailing_active = pos.get("trailing_price") is not None
            entry_price = pos["entry_price"]
            base_lines = [
                f"{pos['side'].upper()}",
                f"Volume: {pos['volume']:,.8f} ({pos['volume'] * last_price:,.2f}€)",
                f"Entry: {entry_price:,.2f}€",
                f"Activation: {pos['activation_price']:,.2f}€",
            ]

            if trailing_active:
                pnl = _pnl_percent(pos, last_price)
                pnl_symbol = "🟢" if pnl and pnl > 0 else "🔴"
                base_lines.extend(
                    [
                        f"Trailing: {pos['trailing_price']:,.2f}€",
                        f"Stop: {pos['stop_price']:,.2f}€",
                        f"PnL: {pnl_symbol} {pnl:+.2f}%",
                    ]
                )

            msg += "\n".join(base_lines) + "\n\n"

        await update.message.reply_text(msg)
    except Exception as e:
        logging.error(f"Error in positions_command: {e}")
        await update.message.reply_text(f"❌ Error fetching positions: {e}")


async def config_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    try:
        pair_filter = context.args[0].upper() if context.args else None
        if pair_filter and pair_filter not in PAIRS:
            await update.message.reply_text(f"❌ Unknown pair: {pair_filter}\nAvailable: {', '.join(PAIRS.keys())}")
            return

        url = f"/config/{pair_filter}" if pair_filter else "/config"
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        items = [data] if pair_filter else data
        msg = "⚙️ Pair Config:\n\n" + "\n\n".join(_format_pair_config(item) for item in items)
        await update.message.reply_text(msg)
    except Exception as e:
        logging.error(f"Error in config_command: {e}")
        await update.message.reply_text(f"❌ Error fetching config: {e}")


async def setconfig_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    usage = (
        "Usage: /setconfig <PAIR> <field> <value>\n\n"
        "Fields:\n"
        "  target       Target allocation %\n"
        "  hodl         Hodl allocation %\n"
        "  kact         Activation ATR multiplier ('none' to disable)\n"
        "  margin       Min price margin from entry\n"
        "  stop-ll/lv/mv/hv/hh   K-stop percentile per volatility level\n\n"
        "Example: /setconfig XBTEUR stop-mv 0.6"
    )
    if len(context.args) != 3:
        await update.message.reply_text(usage)
        return

    pair = context.args[0].upper()
    field = context.args[1].lower()
    value = context.args[2]

    if pair not in PAIRS:
        await update.message.reply_text(f"❌ Unknown pair: {pair}\nAvailable: {', '.join(PAIRS.keys())}")
        return
    if field not in _CONFIG_FIELDS:
        await update.message.reply_text(f"❌ Unknown field: {field}\nFields: {', '.join(_CONFIG_FIELDS)}")
        return

    api_field = _FIELD_MAP[field]
    if value.lower() == "none":
        if field != "kact":
            await update.message.reply_text("❌ Only kact may be set to 'none'.")
            return
        body = {api_field: None}
    else:
        try:
            body = {api_field: float(value)}
        except ValueError:
            await update.message.reply_text(f"❌ Invalid value for {field}: '{value}' is not a number.")
            return

    try:
        resp = await client.patch(f"/config/{pair}", json=body)
        if resp.status_code == 422:
            await update.message.reply_text(f"❌ Invalid: {resp.json().get('detail')}")
            return
        resp.raise_for_status()
        await update.message.reply_text(f"✅ {pair} {field} updated.")
    except Exception as e:
        logging.error(f"Error in setconfig_command: {e}")
        await update.message.reply_text(f"❌ Error updating config: {e}")


def build_tg_app() -> Application:
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("pause", pause_command))
    app.add_handler(CommandHandler("resume", resume_command))
    app.add_handler(CommandHandler("market", market_command))
    app.add_handler(CommandHandler("positions", positions_command))
    app.add_handler(CommandHandler("config", config_command))
    app.add_handler(CommandHandler("setconfig", setconfig_command))
    return app
