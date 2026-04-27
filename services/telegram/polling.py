import asyncio
import logging

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

import core.database as db
from core.config import FIAT_CODE, PAIRS, TELEGRAM_POLL_INTERVAL, TELEGRAM_TOKEN, TELEGRAM_USER_ID
from core.runtime import get_last_balance, get_pair_data

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.bot").setLevel(logging.WARNING)


def _check_auth(update: Update) -> bool:
    if not TELEGRAM_USER_ID:
        return False
    return update.effective_user.id == int(TELEGRAM_USER_ID)


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
        "/help - Show this help\n\n"
        f"Configured pairs: {pairs_list}\n"
        "Example: /market XBTEUR"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    try:
        bot_paused = db.get_bot_paused()
    except Exception as e:
        logging.error(f"Error reading bot status from DB: {e}")
        await update.message.reply_text("❌ Could not read bot status from database.")
        return
    status = "⏸ PAUSED" if bot_paused else "▶️ RUNNING"
    await update.message.reply_text(f"Status: {status}\nPairs: {', '.join(PAIRS.keys())}\n")


async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    try:
        if db.get_bot_paused():
            await update.message.reply_text("⚠️ Bot is already paused.")
            return
        db.set_bot_paused(True, updated_by="telegram")
    except Exception as e:
        logging.error(f"Error updating pause state in DB: {e}")
        await update.message.reply_text("❌ Could not update bot state in database.")
        return
    await update.message.reply_text("⏸ BoTC paused. New operations will not be processed.")


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    try:
        if not db.get_bot_paused():
            await update.message.reply_text("⚠️ Bot is already running.")
            return
        db.set_bot_paused(False, updated_by="telegram")
    except Exception as e:
        logging.error(f"Error updating pause state in DB: {e}")
        await update.message.reply_text("❌ Could not update bot state in database.")
        return
    await update.message.reply_text("▶️ BoTC resumed.")


async def market_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_auth(update):
        return
    try:
        pair_filter = context.args[0].upper() if context.args else None
        if pair_filter and pair_filter not in PAIRS:
            await update.message.reply_text(
                f"❌ Unknown pair: {pair_filter}\nAvailable: {', '.join(PAIRS.keys())}"
            )
            return

        balance = get_last_balance()
        pairs_to_show = [pair_filter] if pair_filter else list(PAIRS.keys())
        msg = "📈 Market Status:\n\n"

        for pair in pairs_to_show:
            try:
                pair_data = get_pair_data(pair)
                price = pair_data.get("last_price")
                atr = pair_data.get("atr")
                volatility_level = pair_data.get("volatility_level", "N/A")
                asset = PAIRS[pair].get("base")
                asset_balance = float(balance.get(asset, 0))
                asset_value_eur = asset_balance * price
                msg += (
                    f"━━━ {pair} ━━━\n"
                    f"Price: {price:,.2f}€\n"
                    f"ATR: {atr:,.2f}€ ({volatility_level})\n"
                    f"Balance: {asset_balance:.8f} ({asset_value_eur:,.2f}€)\n\n"
                )
                if len(pairs_to_show) > 1:
                    await asyncio.sleep(1)
            except Exception as e:
                msg += f"━━━ {pair} ━━━\n❌ Error: {e}\n\n"

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
            await update.message.reply_text(
                f"❌ Unknown pair: {pair_filter}\nAvailable: {', '.join(PAIRS.keys())}"
            )
            return

        pairs_to_show = [pair_filter] if pair_filter else list(PAIRS.keys())
        msg = "📊 Open Positions:\n\n"

        for pair in pairs_to_show:
            pair_data = get_pair_data(pair)
            last_price = pair_data.get("last_price", 0)
            msg += f"━━━ {pair} (Last price: {last_price:,.2f}€) ━━━\n"

            pos = db.load_trailing_state(pair)
            if not pos:
                msg += "⚠️ No open position for this pair.\n\n"
                continue

            trailing_active = pos.get("trailing_price") is not None
            side = pos["side"].lower()
            estimated_value = pos["volume"] * last_price
            entry_price = pos["entry_price"]

            base_lines = [
                f"{pos['side'].upper()}",
                f"Volume: {pos['volume']:,.8f} ({estimated_value:,.2f}€)",
                f"Entry: {entry_price:,.2f}€",
                f"Activation: {pos['activation_price']:,.2f}€",
            ]

            if trailing_active:
                stop_price = pos["stop_price"]
                if side == "sell":
                    estimated_pnl = (stop_price - entry_price) / entry_price * 100
                else:
                    estimated_pnl = (entry_price - stop_price) / entry_price * 100
                pnl_symbol = "🟢" if estimated_pnl > 0 else "🔴"
                base_lines.extend([
                    f"Trailing: {pos['trailing_price']:,.2f}€",
                    f"Stop: {stop_price:,.2f}€",
                    f"PnL: {pnl_symbol} {estimated_pnl:+.2f}%",
                ])

            msg += "\n".join(base_lines) + "\n\n"

        await update.message.reply_text(msg)
    except Exception as e:
        logging.error(f"Error in positions_command: {e}")
        await update.message.reply_text(f"❌ Error fetching positions: {e}")


def build_tg_app():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("pause", pause_command))
    app.add_handler(CommandHandler("resume", resume_command))
    app.add_handler(CommandHandler("market", market_command))
    app.add_handler(CommandHandler("positions", positions_command))
    return app
