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
        self._loop = None
    
    def _check_auth(self, update: Update) -> bool:
        return update.effective_user.id == self.user_id
    
    async def send_startup_message(self):
        try:
            await self.app.bot.send_message(
                chat_id=self.user_id,
                text="🤖 BoTC started and running. Use /help to see available commands."
            )
        except Exception as e:
            logging.error(f"Failed to send startup message: {e}")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_auth(update): return
        await update.message.reply_text(
            "📋 Available commands:\n\n"
            "/status - Bot status\n"
            "/pause - Pause bot operations\n"
            "/resume - Resume bot operations\n"
            "/logs - View last 10 log lines\n"
            "/market - Current market data\n"
            "/positions - Open positions\n"
            "/help - Show this help"
        )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_auth(update): return
        status = "⏸ PAUSED" if BOT_PAUSED else "▶️ RUNNING"
        await update.message.reply_text(
            f"Status: {status}\n"
            f"Last activity: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    async def pause_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_auth(update): return
        global BOT_PAUSED
        if BOT_PAUSED:
            await update.message.reply_text("⚠️ Bot is already paused.")
            return
        BOT_PAUSED = True
        await update.message.reply_text("⏸ BoTC paused. New operations will not be processed.")

    async def resume_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_auth(update): return
        global BOT_PAUSED
        if not BOT_PAUSED:
            await update.message.reply_text("⚠️ Bot is already running.")
            return
        BOT_PAUSED = False
        await update.message.reply_text("▶️ BoTC resumed.")

    async def logs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_auth(update): return
        try:
            with open("logs/BoTC.log", "r", encoding="utf-8") as f:
                lines = f.readlines()[-10:]
            msg = "".join(lines) or "No recent logs."
            # Telegram message limit is 4096 characters
            await update.message.reply_text(f"📋 Latest logs:\n```\n{msg[-3900:]}\n```", parse_mode="Markdown")
        except FileNotFoundError:
            await update.message.reply_text("❌ Log file not found.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error reading logs: {e}")

    async def market_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_auth(update): return
        try:
            price = get_current_price()
            atr = get_current_atr()
            balance = get_balance()
            eur_balance = float(balance.get("ZEUR", 0))
            btc_balance = float(balance.get("XXBT", 0))
            btc_value_eur = btc_balance * price
            total_value = eur_balance + btc_value_eur
            
            msg = (
                f"📈 Market Status:\n"
                f"BTC/EUR: {price:,.2f}€\n"
                f"ATR(15m): {atr:,.2f}€\n\n"
                f"💰 Account Balance:\n"
                f"EUR: {eur_balance:,.2f}€\n"
                f"BTC: {btc_balance:.8f} ({btc_value_eur:,.2f}€)\n"
                f"Total: {total_value:,.2f}€"
            )
            await update.message.reply_text(msg)
        except Exception as e:
            logging.error(f"Error in market_command: {e}")
            await update.message.reply_text(f"❌ Error fetching market status: {e}")

    async def positions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_auth(update): return
        try:
            with open("data/trailing_state.json", "r", encoding="utf-8") as f:
                positions = json.load(f)
            if not positions:
                await update.message.reply_text("ℹ️ No open positions.")
                return
            
            current_price = get_current_price()
            msg = f"📊 Open Positions (Current BTC: {current_price:,.2f}€):\n\n"
            
            for pos_id, pos in positions.items():
                trailing_active = pos.get('trailing_price') is not None

                if trailing_active:
                    trailing_price = pos['trailing_price']
                    stop_price = pos['stop_price']
                    entry_price = pos['entry_price']
                    pnl_pct = ((current_price - entry_price) / entry_price * 100) if pos['side'] == 'buy' else ((entry_price - current_price) / entry_price * 100)
                    pnl_symbol = "🟢" if pnl_pct > 0 else "🔴"
                else:
                    trailing_price = "Not active"
                    stop_price = "Not active"
                    pnl_pct = "N/A"
                    pnl_symbol = ""
                
                msg += (
                    f"━━━━━━━━━━━━━━━\n"
                    f"ID: {pos_id}\n"
                    f"Side: {pos['side'].upper()}\n"
                    f"Entry: {pos['entry_price']:,.2f}€\n"
                    f"Volume: {pos['volume']:,.8f} BTC\n"
                    f"Cost: {pos['cost']:,.2f}€\n"
                    f"Activation: {pos['activation_price']:,.2f}€\n"
                    f"Trailing: {trailing_price}\n"
                    f"Stop: {stop_price}\n"
                    f"P&L: {pnl_symbol} {pnl_pct if isinstance(pnl_pct, str) else f'{pnl_pct:+.2f}%'}\n\n"
                )
            await update.message.reply_text(msg[-4000:])
        except FileNotFoundError:
            await update.message.reply_text("ℹ️ No positions file found.")
        except Exception as e:
            logging.error(f"Error in positions_command: {e}")
            await update.message.reply_text(f"❌ Error fetching positions: {e}")

    async def send_message_async(self, message):
        try:
            await self.app.bot.send_message(chat_id=self.user_id, text=message)
        except Exception as e:
            logging.error(f"Telegram async send error: {e}")

    def send_message(self, message):
        try:
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self.send_message_async(message), self._loop)
            else:
                # Fallback to requests if loop not available
                import requests
                url = f"https://api.telegram.org/bot{self.token}/sendMessage"
                requests.post(url, json={"chat_id": self.user_id, "text": message}, timeout=10)
        except Exception as e:
            logging.error(f"Telegram send error: {e}")

    def run(self):
        # New event loop for this secondary thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        
        try:
            self.app.add_handler(CommandHandler("help", self.help_command))
            self.app.add_handler(CommandHandler("start", self.help_command))
            self.app.add_handler(CommandHandler("status", self.status_command))
            self.app.add_handler(CommandHandler("pause", self.pause_command))
            self.app.add_handler(CommandHandler("resume", self.resume_command))
            self.app.add_handler(CommandHandler("logs", self.logs_command))
            self.app.add_handler(CommandHandler("market", self.market_command))
            self.app.add_handler(CommandHandler("positions", self.positions_command))

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
            self._loop = None
            logging.info("Telegram thread has exited.")

tg_interface = TelegramInterface(TELEGRAM_TOKEN, ALLOWED_USER_ID)

def start_telegram_thread():
    t = threading.Thread(target=tg_interface.run, daemon=True)
    t.start()

def send_notification(msg):
    tg_interface.send_message(msg)