import os
import logging
import re
import sqlite3
import threading
from datetime import datetime, date

from flask import Flask
from telegram.ext import Updater, MessageHandler, Filters, CommandHandler

# =============== CONFIG ===============
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# REPLACE these with your real IDs from /chatid
MAIN_CHAT_ID = -4850657873      # Payment group ID
SUMMARY_CHAT_ID = -1003387786870   # Summary/report group ID
ADMIN_IDS = [123456789]

DB_FILE = "payments.db"
# =====================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- Tiny web server for Render ----------
app = Flask(__name__)

@app.route("/")
def index():
    return "QR Summary Bot is running."

def run_http_server():
    port = int(os.environ.get("PORT", "8000"))
    logger.info(f"Starting HTTP server on port {port}")
    app.run(host="0.0.0.0", port=port)
    

# ---------- DB SETUP ----------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            msg_id INTEGER,
            amount REAL,
            currency TEXT,
            paid_at TEXT,
            raw_text TEXT
        );
        """
    )
    conn.commit()
    conn.close()


# ---------- PARSING ----------
AMT_PATTERN = re.compile(r"Received\s+([\d,]+(?:\.\d+)?)\s+(USD|KHR)")
DT_PATTERN1 = re.compile(r"on\s+(\d{2}-[A-Za-z]{3}-\d{4})\s+(\d{1,2}:\d{2}[AP]M)")
DT_PATTERN2 = re.compile(r",\s*(\d{2}-[A-Za-z]{3}-\d{4})\s+(\d{1,2}:\d{2}[AP]M)")


def parse_payment(text: str):
    m_amt = AMT_PATTERN.search(text)
    if not m_amt:
        return None

    amount_str, currency = m_amt.groups()
    amount = float(amount_str.replace(",", ""))

    m_dt = DT_PATTERN1.search(text) or DT_PATTERN2.search(text)
    if not m_dt:
        return None

    date_str, time_str = m_dt.groups()
    try:
        paid_at = datetime.strptime(f"{date_str} {time_str}", "%d-%b-%Y %I:%M%p")
    except ValueError:
        return None

    return {"amount": amount, "currency": currency, "paid_at": paid_at}


# ---------- DB HELPERS ----------
def save_payment(chat_id, msg_id, parsed, raw_text):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO payments (chat_id, msg_id, amount, currency, paid_at, raw_text)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            chat_id,
            msg_id,
            parsed["amount"],
            parsed["currency"],
            parsed["paid_at"].isoformat(),
            raw_text,
        ),
    )
    conn.commit()
    conn.close()


def summarize_by_date(day: date):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT currency, COUNT(*), SUM(amount)
        FROM payments
        WHERE date(paid_at) = ?
        GROUP BY currency
        """,
        (day.isoformat(),),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def summarize_by_month(year: int, month: int):
    ym = f"{year:04d}-{month:02d}"
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT currency, COUNT(*), SUM(amount)
        FROM payments
        WHERE strftime('%Y-%m', paid_at) = ?
        GROUP BY currency
        """,
        (ym,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ---------- HELPERS ----------
def send_summary_text(context, text: str):
    try:
        context.bot.send_message(chat_id=SUMMARY_CHAT_ID, text=text)
    except Exception as e:
        logger.error("Failed to send summary: %s", e)


# ---------- HANDLERS ----------
def payment_message(update, context):
    msg = update.effective_message
    logger.info(f"Got message in chat {msg.chat_id}: {msg.text!r}")

    if msg.chat_id != MAIN_CHAT_ID:
        return

    text = msg.text or ""
    parsed = parse_payment(text)

    if parsed:
        save_payment(
            chat_id=msg.chat_id,
            msg_id=msg.message_id,
            parsed=parsed,
            raw_text=text,
        )
        logger.info("Saved payment: %s", parsed)
    else:
        logger.info("Message didn't match payment pattern.")


def cmd_today(update, context):
    today = date.today()
    rows = summarize_by_date(today)

    if not rows:
        send_summary_text(context, f"No payments recorded for {today.isoformat()}.")
        return

    lines = [f"Summary for {today.isoformat()}:"]
    for currency, count, total in rows:
        lines.append(f"- {currency}: {total:,.2f} ({count} tx)")
    send_summary_text(context, "\n".join(lines))


def cmd_day(update, context):
    if not context.args:
        send_summary_text(context, "Usage: /day YYYY-MM-DD")
        return
    try:
        d = datetime.strptime(context.args[0], "%Y-%m-%d").date()
    except ValueError:
        send_summary_text(context, "Invalid date format. Use YYYY-MM-DD.")
        return

    rows = summarize_by_date(d)
    if not rows:
        send_summary_text(context, f"No payments recorded for {d.isoformat()}.")
        return

    lines = [f"Summary for {d.isoformat()}:"]
    for currency, count, total in rows:
        lines.append(f"- {currency}: {total:,.2f} ({count} tx)")
    send_summary_text(context, "\n".join(lines))


def cmd_month(update, context):
    if context.args:
        try:
            year, month = map(int, context.args[0].split("-"))
        except Exception:
            send_summary_text(context, "Usage: /month or /month YYYY-MM")
            return
    else:
        today = date.today()
        year, month = today.year, today.month

    rows = summarize_by_month(year, month)
    ym = f"{year:04d}-{month:02d}"

    if not rows:
        send_summary_text(context, f"No payments recorded for {ym}.")
        return

    lines = [f"Summary for {ym}:"]
    for currency, count, total in rows:
        lines.append(f"- {currency}: {total:,.2f} ({count} tx)")
    send_summary_text(context, "\n".join(lines))


def start(update, context):
    update.message.reply_text(
        "QR Summary Bot running.\nUse /today, /day YYYY-MM-DD, /month."
    )


def chatid(update, context):
    chat = update.effective_chat
    chat_id = chat.id
    title = chat.title or getattr(chat, "full_name", "") or "Private chat"
    update.message.reply_text(f"Chat ID: {chat_id}\nTitle: {title}")

def is_admin(update):
    user = update.effective_user
    return user and user.id in ADMIN_IDS


def cmd_resetdb(update, context):
    # Only allow admins
    if not is_admin(update):
        # Optional: silently ignore or send a warning
        return

    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("DELETE FROM payments;")
        conn.commit()
        conn.close()
        msg = "✅ All payment data has been cleared."
        # Reply in summary group so you always see it there
        context.bot.send_message(chat_id=SUMMARY_CHAT_ID, text=msg)
    except Exception as e:
        logger.error("Error resetting DB: %s", e)
        context.bot.send_message(
            chat_id=SUMMARY_CHAT_ID,
            text="❌ Failed to reset data. Check logs."
        )

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set.")

    init_db()

    # Start tiny HTTP server in another thread (for Render port check)
    threading.Thread(target=run_http_server, daemon=True).start()

    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("chatid", chatid))
    dp.add_handler(CommandHandler("today", cmd_today))
    dp.add_handler(CommandHandler("day", cmd_day))
    dp.add_handler(CommandHandler("month", cmd_month))
    dp.add_handler(CommandHandler("resetdb", cmd_resetdb))

    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, payment_message))

    logger.info("QR Summary Bot started. Listening for payments...")
    updater.start_polling()
    updater.idle()




if __name__ == "__main__":
    main()


