#!/usr/bin/env python3
"""
tg_diag.py - Telegram connectivity test (adapted from ethbot scripts/tg_diag.py)

Run on VPS or locally:
    python tg_diag.py

It will:
1. Print bot identity (getMe)
2. Delete any webhook (required for polling)
3. Send a test message to your ADMIN_CHAT_ID

Uses the same env var aliases as the main bot.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

def get_token():
    return (
        os.getenv("TELEGRAM_TOKEN")
        or os.getenv("BOT_TOKEN")
        or os.getenv("TG_BOT_TOKEN")
        or ""
    )

def get_chat_id():
    return (
        os.getenv("ADMIN_CHAT_ID")
        or os.getenv("TELEGRAM_CHAT_ID")
        or os.getenv("TG_USER_ID")
        or ""
    )

def main():
    token = get_token()
    chat_id = get_chat_id()

    if not token or not chat_id:
        print("❌ Missing TELEGRAM_TOKEN (or BOT_TOKEN / TG_BOT_TOKEN) and/or ADMIN_CHAT_ID (or TELEGRAM_CHAT_ID / TG_USER_ID)")
        print("   Put them in .env and try again.")
        return

    print("=== 1) Bot identity ===")
    try:
        me = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10).json()
        print(me)
        if me.get("ok"):
            print(f"✅ Bot: @{me['result'].get('username')} (id={me['result'].get('id')})")
    except Exception as e:
        print(f"getMe failed: {e}")
        return

    print("\n=== 2) Webhook cleanup (for polling) ===")
    try:
        wh = requests.get(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=10).json()
        if wh.get("result", {}).get("url"):
            print(f"Webhook was set: {wh['result']['url']}")
            del_resp = requests.post(
                f"https://api.telegram.org/bot{token}/deleteWebhook",
                json={"drop_pending_updates": True},
                timeout=10,
            ).json()
            print("deleteWebhook:", del_resp)
        else:
            print("No webhook set (good for polling).")
    except Exception as e:
        print(f"Webhook check failed: {e}")

    print("\n=== 3) Test message ===")
    try:
        payload = {
            "chat_id": chat_id,
            "text": "✅ B20 bot tg_diag.py — token + chat_id working!\n\nIf you see this, your Telegram setup is correct.",
            "disable_web_page_preview": True,
        }
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=15,
        )
        print(r.json())
        if r.ok:
            print("✅ Test message sent successfully. Check your Telegram.")
        else:
            print("❌ Send failed.")
    except Exception as e:
        print(f"sendMessage failed: {e}")

if __name__ == "__main__":
    main()