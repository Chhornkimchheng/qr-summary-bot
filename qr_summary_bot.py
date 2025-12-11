import os
import logging
import re
import sqlite3
from datetime import datetime, date

from telegram.ext import Updater, MessageHandler, Filters, CommandHandler

# =============== CONFIG ===============
# Get token from environment (Render)
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# REPLACE these with your REAL IDs (no x)
MAIN_CHAT_ID = -1002424205110      # Payment group ID
SUMMARY_CHAT_ID = -1003387786870   # Summary/report group ID

DB_FILE = "payments.db"
# =====================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


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
            paid_at TEXT,   -- ISO datetime string
            raw_text TEXT
        );
        """
    )
    conn.commit()
    conn.close()


# ---------- PARSING ----------
# Examples (from you):
# Received  26.00 USD from POV TEAB,Wing Bank (Cambodia) Plc by KHQR,on 11-Dec-2025 03:46PM, at SNC shop by SN  (Hash. b818c4e3).
# Received 10.50 USD from 097 2797 496 SEAB SOKNA, 11-Dec-2025 03:33PM. Ref.ID: 53453274040, at SNC SHOP BY SN.
# Received  801,000 KHR from KIMCHHENG CHHORN,ABA Bank by KHQR,on 11-Dec-2025 03:03PM, at SNC shop by SN  (Hash. adb8a3d1).

AMT_PATTERN = re.compile(r"Received\s+([\d,]+(?:\.\d+)?)\s+(USD|KHR)")
DT_PATTERN1 = re.compile(r"on\s+(\d{2}-[A-Za-z]{3}-\d{4})\s+(\d{1,2}:\d{2}[AP]M)")
DT_PATTERN2 = re.compile(r",\s*(\d{2}-[A-Za-z]{3}-\d{4})\s+(\d{1,2}:\d{2}[AP]M)")


def parse_payment(text: str):
    """
    Parse a payment message.
    Returns dict {amount, currency, paid_at} or None if not match.
    paid_at is datetime object.
    """
    m_amt = AMT_PATTERN.search(text)
    if not m_amt:
        return None

    amount_str, currency = m_amt.groups()
    amount = float(amount_str.replace(",", ""))

    m_dt = DT_PATTERN1.search(text) or DT_PATTERN2.search(text)
    if not m_dt:
        # If no date/time found, you can choose to use "now" instead:
        # paid_at = datetime.now()
        return None

    date_str, time_str = m_dt.groups()
    # Example: 11-Dec-2025 03:46PM
    try:
        paid_at = datetime.strptime(f"{date_str} {time_str}", "%d-%b-%Y %I:%M%p")
    except ValueError:
        return None

    return {
        "amount": amount,
        "currency": currency,
        "paid_at": paid_at,
    }


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
            parsed["paid_at"].isoformat(),  # store as ISO string
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


# ---------- UTIL ----------
def send_summary_text(context, text: str):
    """
    Always send reports to SUMMARY_CHAT_ID
    (so even if you run /today somewhere else, it goes to report group).
    """
    try:
        context.bot.send_message(chat_id=SUMMARY_CHAT_ID, text=text)
    except Exception as e:
        logger.error("Failed to send summary: %s", e)


# ---------- HANDLERS ----------
def payment_message(update, context):
    msg = update.effective_message

    # Only listen to MAIN payment group
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
    # usage: /day 2025-12-11
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
    # usage: /month or /month 2025-12
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


# Keep /chatid for future debugging
def chatid(update, context):
    chat = update.effective_chat
    chat_id = chat.id
    title = chat.title or getattr(chat, "full_name", "") or "Private chat"
    update.message.reply_text(f"Chat ID: {chat_id}\nTitle: {title}")


def start(update, context):
    update.message.reply_text(
        "QR Summary Bot is running.\n"
        "Use /today, /day YYYY-MM-DD, /month in the summary group."
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Please set it in Render.")

    init_db()

    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Commands
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("chatid", chatid))
    dp.add_handler(CommandHandler("today", cmd_today))
    dp.add_handler(CommandHandler("day", cmd_day))
    dp.add_handler(CommandHandler("month", cmd_month))

    # All text messages in MAIN group (payments)
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, payment_message))

    logger.info("QR Summary Bot started. Listening for payments...")
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
