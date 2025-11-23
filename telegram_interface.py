import logging
import threading
import time
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

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != self.user_id: return
        await update.message.reply_text("🤖 Bot de Trading Online. Usa /status, /pause, /resume o /logs.")

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != self.user_id: return
        status = "⏸ PAUSADO" if BOT_PAUSED else "▶️ EJECUTANDO"
        await update.message.reply_text(f"Estado: {status}\nÚltima actividad: {time.strftime('%H:%M:%S')}")

    async def pause_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != self.user_id: return
        global BOT_PAUSED
        BOT_PAUSED = True
        await update.message.reply_text("⏸ Bot pausado. No se procesarán nuevas operaciones.")

    async def resume_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != self.user_id: return
        global BOT_PAUSED
        BOT_PAUSED = False
        await update.message.reply_text("▶️ Bot reanudado.")

    async def logs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != self.user_id: return
        try:
            with open("logs/BoTC.log", "r", encoding="utf-8") as f:
                lines = f.readlines()[-15:]
            msg = "".join(lines) or "No hay logs recientes."
            await update.message.reply_text(f"📋 Últimos logs:\n{msg[-4000:]}")
        except Exception as e:
            await update.message.reply_text(f"Error leyendo logs: {e}")

    def send_message(self, message):
        try:
            import requests
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            requests.post(url, json={"chat_id": self.user_id, "text": message})
        except Exception as e:
            logging.error(f"Telegram send error: {e}")

    def run(self):
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        self.app.add_handler(CommandHandler("pause", self.pause_command))
        self.app.add_handler(CommandHandler("resume", self.resume_command))
        self.app.add_handler(CommandHandler("logs", self.logs_command))
        
        self.app.run_polling()

tg_interface = TelegramInterface(TELEGRAM_TOKEN, ALLOWED_USER_ID)

def start_telegram_thread():
    t = threading.Thread(target=tg_interface.run, daemon=True)
    t.start()

def send_notification(msg):
    tg_interface.send_message(msg)