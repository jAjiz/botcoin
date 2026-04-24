import threading, time, logging, asyncio, requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from core.config import TELEGRAM_TOKEN, TELEGRAM_USER_ID, TELEGRAM_POLL_INTERVAL, PAIRS, FIAT_CODE
from core.runtime import get_last_balance, get_pair_data, get_trailing_state
import core.database as db

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
        pairs_list = ', '.join(PAIRS.keys())
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

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_auth(update): return
        try:
            bot_paused = db.get_bot_paused()
        except Exception as e:
            logging.error(f"Error reading bot status from DB: {e}")
            await update.message.reply_text("❌ Could not read bot status from database.")
            return
        status = "⏸ PAUSED" if bot_paused else "▶️ RUNNING"
        pairs_list = ', '.join(PAIRS.keys())
        await update.message.reply_text(
            f"Status: {status}\n"
            f"Last activity: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"Pairs: {pairs_list}\n"
        )

    async def pause_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_auth(update): return
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

    async def resume_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_auth(update): return
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

    async def market_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_auth(update): return
        try:
            # Check if a specific pair was requested
            pair_filter = context.args[0].upper() if context.args else None
            if pair_filter and pair_filter not in PAIRS:
                await update.message.reply_text(f"❌ Unknown pair: {pair_filter}\nAvailable: {', '.join(PAIRS.keys())}")
                return
            
            balance = get_last_balance()
            pairs_to_show = [pair_filter] if pair_filter else list(PAIRS.keys())
            
            msg = "📈 Market Status:\n\n"
            
            for pair in pairs_to_show:
                try:
                    pair_data = get_pair_data(pair)
                    price = pair_data.get('last_price')
                    atr = pair_data.get('atr')
                    volatility_level = pair_data.get('volatility_level', 'N/A')

                    asset = PAIRS[pair].get('base')
                    asset_balance = float(balance.get(asset, 0))
                    asset_value_eur = asset_balance * price
                    
                    msg += (
                        f"━━━ {pair} ━━━\n"
                        f"Price: {price:,.2f}€\n"
                        f"ATR: {atr:,.2f}€ ({volatility_level})\n"
                        f"Balance: {asset_balance:.8f} ({asset_value_eur:,.2f}€)\n\n"
                    )
                    if len(pairs_to_show) > 1:
                        await asyncio.sleep(1)  # Delay to avoid rate limits
                except Exception as e:
                    msg += f"━━━ {pair} ━━━\n❌ Error: {e}\n\n"
            
            fiat_balance = float(balance.get(FIAT_CODE, 0.0))
            msg += f"{FIAT_CODE} Balance: {fiat_balance:,.2f}€"
            
            await update.message.reply_text(msg)
        except Exception as e:
            logging.error(f"Error in market_command: {e}")
            await update.message.reply_text(f"❌ Error fetching market status: {e}")

    async def positions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_auth(update): return
        try:
            # Check if a specific pair was requested
            pair_filter = context.args[0].upper() if context.args else None
            if pair_filter and pair_filter not in PAIRS:
                await update.message.reply_text(f"❌ Unknown pair: {pair_filter}\nAvailable: {', '.join(PAIRS.keys())}")
                return
            
            all_positions = get_trailing_state()
            pairs_to_show = [pair_filter] if pair_filter else list(PAIRS.keys())
            msg = "📊 Open Positions:\n\n"
            
            for pair in pairs_to_show:
                pair_data = get_pair_data(pair)
                last_price = pair_data.get('last_price', 0)
                msg += f"━━━ {pair} (Last price: {last_price:,.2f}€) ━━━\n"

                pos = all_positions.get(pair)
                if not pos:
                    msg += "⚠️ No open position for this pair.\n\n"
                    continue
                
                trailing_active = pos.get('trailing_price') is not None
                side = pos['side'].lower()
                estimated_value = pos['volume'] * last_price
                entry_price = pos['entry_price']
                activation_price = pos['activation_price']

                # Base lines
                base_lines = [
                    f"{pos['side'].upper()}",
                    f"Volume: {pos['volume']:,.8f} ({estimated_value:,.2f}€)",
                    f"Entry: {entry_price:,.2f}€",
                    f"Activation: {activation_price:,.2f}€"
                ]                

                if trailing_active:
                    # Active: show full trailing info and P&L
                    stop_price = pos['stop_price']

                    if side == 'sell':
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
            self.app.add_handler(CommandHandler("status", self.status_command))
            self.app.add_handler(CommandHandler("pause", self.pause_command))
            self.app.add_handler(CommandHandler("resume", self.resume_command))
            self.app.add_handler(CommandHandler("market", self.market_command))
            self.app.add_handler(CommandHandler("positions", self.positions_command))

            loop.run_until_complete(self.send_startup_message())

            self.app.run_polling(
                poll_interval=TELEGRAM_POLL_INTERVAL, 
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

tg_interface = None

def initialize_telegram():
    global tg_interface
    tg_interface = TelegramInterface(TELEGRAM_TOKEN, int(TELEGRAM_USER_ID))
    t = threading.Thread(target=tg_interface.run, daemon=True)
    t.start()
    
def send_notification(msg):
    if tg_interface is None:
        logging.warning("Telegram not initialized. Message not sent: " + msg)
        return
    tg_interface.send_message(msg)

def stop_telegram_thread():
    try:
        if tg_interface and tg_interface.app and tg_interface._loop and tg_interface._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(tg_interface.app.stop(), tg_interface._loop)
            try:
                future.result(timeout=5) # Wait for stop to complete
            except Exception as e:
                logging.warning(f"Timeout/err stopping Telegram app: {e}")
            time.sleep(0.5)
            logging.info("Telegram thread stopped.")
        else:
            logging.info("Telegram app not running or loop not available.")
    except Exception as e:
        logging.error(f"Error stopping Telegram thread: {e}")