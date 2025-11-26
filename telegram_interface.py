import threading
import time
import logging
import asyncio
import json
from kraken_client import get_current_price, get_current_atr, get_balance
from config import TELEGRAM_TOKEN, ALLOWED_USER_ID
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

POLL_INTERVAL_SEC = 30
BOT_PAUSED = False

# Only log warnings and above from telegram library
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.bot").setLevel(logging.WARNING)

class TelegramInterface:
    def __init__(self, token, user_id):
        self.token = token
        self.user_id = user_id
        self.app = ApplicationBuilder().token(token).build()
    
    async def send_startup_message(self):
        try:
            await self.app.bot.send_message(
                chat_id=self.user_id,
                text="🤖 BoTC started and running. Use:\n"
                "/status\n"
                "/pause\n"
                "/resume\n"
                "/logs\n"
                "/market\n"
                "/positions"
            )
        except Exception as e:
            logging.error(f"Failed to send startup message: {e}")
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != self.user_id: return
        status = "⏸ PAUSED" if BOT_PAUSED else "▶️ RUNNING"
        await update.message.reply_text(f"Status: {status}\nLast activity: {time.strftime('%H:%M:%S')}")

    async def pause_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != self.user_id: return
        global BOT_PAUSED
        BOT_PAUSED = True
        await update.message.reply_text("⏸ BoTC paused. New operations will not be processed.")

    async def resume_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != self.user_id: return
        global BOT_PAUSED
        BOT_PAUSED = False
        await update.message.reply_text("▶️ BoTC resumed.")

    async def logs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != self.user_id: return
        try:
            with open("logs/BoTC.log", "r", encoding="utf-8") as f:
                lines = f.readlines()[-10:]
            msg = "".join(lines) or "No recent logs."
            await update.message.reply_text(f"📋 Latest logs:\n{msg[-4000:]}")
        except Exception as e:
            await update.message.reply_text(f"Error reading logs: {e}")

    async def market_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != self.user_id: return
        try:
            price = get_current_price()
            atr = get_current_atr()
            balance = get_balance()
            eur_balance = float(balance.get("ZEUR", 0))
            btc_balance = float(balance.get("XXBT", 0))
            msg = (
                f"📈 Market Status:\n"
                f"Current BTC/EUR Price: {price:,.1f}€\n"
                f"Current ATR(15m): {atr:,.1f}€\n\n"
                f"💰 Account Balance:\n"
                f"EUR: {eur_balance:,.2f}€\n"
                f"BTC: {btc_balance:,.8f} BTC"
            )
            await update.message.reply_text(msg)
        except Exception as e:
            await update.message.reply_text(f"Error fetching market status: {e}")

    async def positions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != self.user_id: return
        try:
            with open("positions.json", "r", encoding="utf-8") as f:
                positions = json.load(f)
            if not positions:
                await update.message.reply_text("No open positions.")
                return
            msg = "📊 Open Positions:\n"
            for pos_id, pos in positions.items():
                msg += (
                    f"ID: {pos_id}\n"
                    f"Side: {pos['side']}\n"
                    f"Entry Price: {pos['entry_price']:,.1f}€\n"
                    f"Cost: {pos['cost']:,.2f}€\n"
                    f"Activation Price: {pos['activation_price']:,.1f}€\n"
                    f"Trailing Price: {pos['trailing_price']:,.1f}€\n"
                    f"Stop Price: {pos['stop_price']:,.1f}€\n"
                )
            await update.message.reply_text(msg[-4000:])
        except Exception as e:
            await update.message.reply_text(f"Error fetching positions: {e}")

    def send_message(self, message):
        try:
            import requests
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            requests.post(url, json={"chat_id": self.user_id, "text": message})
        except Exception as e:
            logging.error(f"Telegram send error: {e}")

    def run(self):
        # New event loop for this secondary thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            self.app.add_handler(CommandHandler("status", self.status_command))
            self.app.add_handler(CommandHandler("pause", self.pause_command))
            self.app.add_handler(CommandHandler("resume", self.resume_command))
            self.app.add_handler(CommandHandler("logs", self.logs_command))
            self.app.add_handler(CommandHandler("positions", self.positions_command))

            # Send startup message before starting polling
            loop.run_until_complete(self.send_startup_message())

            self.app.run_polling(
                poll_interval=POLL_INTERVAL_SEC, 
                stop_signals=None, 
                close_loop=False
            )
        except Exception as e:
            logging.error(f"Telegram thread error: {e}")
        finally:
            try:
                if loop.is_running():
                    loop.close()
            except:
                pass
            logging.info("Telegram thread has exited.")

tg_interface = TelegramInterface(TELEGRAM_TOKEN, ALLOWED_USER_ID)

def start_telegram_thread():
    t = threading.Thread(target=tg_interface.run, daemon=True)
    t.start()

def send_notification(msg):
    tg_interface.send_message(msg)