import threading, time, logging, asyncio, json, requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from exchange.kraken import get_last_price, get_current_atr, get_balance
from core.config import TELEGRAM_TOKEN, ALLOWED_USER_ID, POLL_INTERVAL_SEC, MODE, PAIRS

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
        status = "⏸ PAUSED" if BOT_PAUSED else "▶️ RUNNING"
        pairs_list = ', '.join(PAIRS.keys())
        await update.message.reply_text(
            f"Status: {status}\n"
            f"Last activity: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"Mode: {MODE.upper()}\n"
            f"Pairs: {pairs_list}\n"
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

    async def market_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_auth(update): return
        try:
            # Check if a specific pair was requested
            pair_filter = context.args[0].upper() if context.args else None
            if pair_filter and pair_filter not in PAIRS:
                await update.message.reply_text(f"❌ Unknown pair: {pair_filter}\nAvailable: {', '.join(PAIRS.keys())}")
                return
            
            balance = get_balance()
            pairs_to_show = [pair_filter] if pair_filter else list(PAIRS.keys())
            
            # Build market status section
            market_lines = ["📈 Market Status:"]
            total_assets_value_eur = 0.0
            assets_seen = []

            for pair in pairs_to_show:
                try:
                    wsname = PAIRS[pair]['wsname'] or pair
                    price = get_last_price(PAIRS[pair]['primary'])
                    atr = get_current_atr(PAIRS[pair]['wsname'] or pair)
                    market_lines.append(f"{wsname}: {price:,.2f}€ | ATR(15m): {atr:,.2f}€")
                    # Accumulate asset value for totals
                    asset = PAIRS[pair]['base']
                    asset_balance = float(balance.get(asset, 0))
                    total_assets_value_eur += asset_balance * price
                    assets_seen.append((asset, asset_balance, price))
                    if len(pairs_to_show) > 1:
                        await asyncio.sleep(1)  # Delay to avoid rate limits
                except Exception as e:
                    market_lines.append(f"{pair}: ❌ Error: {e}")

            # Build account balance section
            balance_lines = ["", "💰 Account Balance:"]
            eur_balance = float(balance.get("ZEUR", 0))
            balance_lines.append(f"EUR: {eur_balance:,.2f}€")
            # Deduplicate assets and print their EUR value
            printed_assets = set()
            for asset, amount, price in assets_seen:
                if asset in printed_assets:
                    continue
                printed_assets.add(asset)
                asset_value_eur = amount * price
                # Pretty name: strip leading 'X'/'Z'
                pretty = asset.replace('X', '').replace('Z', '')
                balance_lines.append(f"{pretty}: {amount:.8f} ({asset_value_eur:,.2f}€)")

            total_value_eur = eur_balance + total_assets_value_eur
            balance_lines.append(f"Total: {total_value_eur:,.2f}€")

            msg = "\n".join(market_lines + balance_lines)
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
            
            with open("data/trailing_state.json", "r", encoding="utf-8") as f:
                all_positions = json.load(f)
            
            pairs_to_show = [pair_filter] if pair_filter else list(PAIRS.keys())
            msg = "📊 Open Positions:\n\n"
            total_positions = 0
            
            for pair in pairs_to_show:
                pair_positions = all_positions.get(pair, {})
                if not pair_positions:
                    continue
                
                try:
                    current_price = get_last_price(PAIRS[pair]['primary'])
                    msg += f"━━━ {pair} (Price: {current_price:,.2f}€) ━━━\n"
                    
                    for pos_id, pos in pair_positions.items():
                        total_positions += 1
                        trailing_active = pos.get('trailing_price') is not None

                        side = pos.get('side', '').lower()
                        entry_price = pos.get('entry_price')
                        activation_price = pos.get('activation_price')

                        # Header with active icon if trailing is active
                        active_icon = "⚡" if trailing_active else ""  # highlight active

                        # Base lines
                        base_lines = [
                            f"{active_icon} ID: {pos_id}",
                            f"Side: {pos['side'].upper()} | Entry: {entry_price:,.2f}€",
                        ]

                        # Show either volume or cost depending on side
                        if side == 'sell':
                            base_lines.append(f"Volume: {pos['volume']:,.8f}")
                        elif side == 'buy':
                            base_lines.append(f"Cost: {pos['cost']:,.2f}€")

                        if not trailing_active:
                            # Not active: show activation only
                            base_lines.append(f"Activation: {activation_price:,.2f}€")
                            msg += "\n".join(base_lines) + "\n\n"
                        else:
                            # Active: show full trailing info and P&L
                            trailing_price = pos.get('trailing_price')
                            stop_price = pos.get('stop_price')
                            if side == 'sell':
                                pnl_pct = ((stop_price - entry_price) / entry_price * 100)
                            else:
                                pnl_pct = ((entry_price - stop_price) / entry_price * 100)
                            pnl_symbol = "🟢" if pnl_pct > 0 else "🔴"

                            base_lines.extend([
                                f"Activation: {activation_price:,.2f}€",
                                f"Trailing: {trailing_price:,.2f}€",
                                f"Stop: {stop_price:,.2f}€",
                                f"P&L: {pnl_symbol} {pnl_pct:+.2f}%",
                            ])
                            msg += "\n".join(base_lines) + "\n\n"
                    
                    if len(pairs_to_show) > 1:
                        await asyncio.sleep(1)  # Delay to avoid rate limits
                except Exception as e:
                    msg += f"❌ Error fetching {pair}: {e}\n\n"
            
            if total_positions == 0:
                await update.message.reply_text("ℹ️ No open positions.")
            else:
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