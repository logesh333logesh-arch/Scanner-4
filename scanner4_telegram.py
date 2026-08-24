"""
Scanner 4 - Telegram Integration
------------------------------------
Sends scan signals to Telegram via bot API.

Setup (one-time):
    export TELEGRAM_BOT_TOKEN="8641343889:AAFt1kFRva8PBZr-gRu6NqmJ8MnhN8JmBAM"
    export TELEGRAM_CHAT_ID="1155276244"

Usage in scanner4_main.py:
    from scanner4_telegram import send_signals_to_telegram
    send_signals_to_telegram(signals)
"""

import os
import requests

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram_message(text: str, bot_token: str = None, chat_id: str = None) -> bool:
    """
    Sends a single text message to the configured Telegram chat.
    Falls back to TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars if not
    passed explicitly. Returns True on success, False on failure (never
    raises - a Telegram hiccup shouldn't crash the scanner).
    """
    bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("[WARN] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set - skipping Telegram send")
        return False

    url = TELEGRAM_API_URL.format(token=bot_token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",  # allows <b>bold</b> etc. in messages
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[WARN] Telegram send failed: {e}")
        return False


def send_signals_to_telegram(signals: list, bot_token: str = None, chat_id: str = None) -> None:
    """
    Sends each signal as a separate Telegram message (keeps messages short
    and readable on mobile). If there are no signals, sends a short
    'no signals today' notice instead of staying silent - so you know the
    scanner actually ran.
    """
    from scanner4_cpr_calculator import format_signal_message

    if not signals:
        send_telegram_message("📊 Scanner 4: No signals today (depth >= threshold not met on any strike).",
                               bot_token, chat_id)
        return

    header = f"📊 <b>Scanner 4 — {len(signals)} signal(s) found</b>"
    send_telegram_message(header, bot_token, chat_id)

    for r in signals:
        msg = format_signal_message(r)
        # wrap strike name in bold for readability
        lines = msg.split("\n")
        lines[0] = f"<b>{lines[0]}</b>"
        send_telegram_message("\n".join(lines), bot_token, chat_id)


# ============================================================
# QUICK TEST
# ============================================================
if __name__ == "__main__":
    ok = send_telegram_message("✅ Scanner 4 Telegram test message - if you see this, it's working!")
    print("Send success:", ok)
