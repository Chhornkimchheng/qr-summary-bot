import os
import logging
from telegram.ext import Updater, CommandHandler

# ====== CONFIG ======
# Get token from environment (Render)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
# =====================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def start(update, context):
    update.message.reply_text("Hello! Send /chatid here and I’ll show this chat’s ID.")


def chatid(update, context):
    chat = update.effective_chat
    chat_id = chat.id
    title = chat.title or chat.full_name or "Private chat"
    update.message.reply_text(f"Chat ID: {chat_id}\nTitle: {title}")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Please set it in Render.")

    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Commands
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("chatid", chatid))

    logger.info("Bot started. Waiting for /chatid...")
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
