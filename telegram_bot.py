#!/usr/bin/env python3
"""
telegram_bot.py - Interactive Telegram bot for B20 Sniper (ethbot style)

Uses python-telegram-bot library for reliable polling + callbacks.

Outbound alerts (from monitor, buys, etc.) still use the simple requests tg_send
in the main file for decoupling.

Run polling in background thread so the sniper monitor can continue.
"""

import os
import asyncio
import threading
import logging
from typing import Optional, Callable, Any

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

load_dotenv()

# --- Config resolution (ethbot style) ---
def _get_token() -> str:
    return (
        os.getenv("TELEGRAM_TOKEN")
        or os.getenv("BOT_TOKEN")
        or os.getenv("TG_BOT_TOKEN")
        or ""
    )

def _get_chat_id() -> str:
    return (
        os.getenv("ADMIN_CHAT_ID")
        or os.getenv("TELEGRAM_CHAT_ID")
        or os.getenv("TG_USER_ID")
        or ""
    )

# These will be set by the main sniper before starting the bot
_current_w3 = None
_cfg = None
_buy_callback: Optional[Callable] = None   # function(token: str, amount: float, cfg: dict, w3: Any)


def set_sniper_context(w3, cfg: dict, buy_callback: Callable):
    """Called by main sniper to inject context for buy buttons etc."""
    global _current_w3, _cfg, _buy_callback
    _current_w3 = w3
    _cfg = cfg
    _buy_callback = buy_callback


async def _send_control_panel(update_or_chat, context: ContextTypes.DEFAULT_TYPE = None):
    """Show the main control buttons."""
    keyboard = [
        [
            InlineKeyboardButton("📊 Status", callback_data="status"),
            InlineKeyboardButton("⏸️ Pause", callback_data="pause"),
        ],
        [
            InlineKeyboardButton("▶️ Resume", callback_data="resume"),
            InlineKeyboardButton("🛑 Kill", callback_data="kill"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "B20 Bot Control Panel (LIVE):"

    if hasattr(update_or_chat, "message"):
        # from command
        await update_or_chat.message.reply_text(text, reply_markup=reply_markup)
    else:
        # from callback or direct
        chat_id = update_or_chat
        # We use a global app bot if available, but for simplicity we rely on the main tg_send for now
        # The library bot is available inside handlers
        pass  # handled in caller for now


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_control_panel(update, context)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Enhanced status (upgrade #77) with positions
    status_msg = "📊 Bot Status: LIVE mode active.\nMonitoring pools + B20Factory.\nTG interactive + buttons ready.\n"
    try:
        # Try to show active positions if shared, but since separate, use DB
        import sqlite3
        conn = sqlite3.connect("/home/ubuntu/b20-bot/b20_trades.db")
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM trades WHERE status='success' AND action='buy'")
        buys = c.fetchone()[0]
        conn.close()
        status_msg += f"Successful buys so far: {buys}\n"
    except:
        pass
    status_msg += "Check logs for recent snipes.\nUse /positions for trade history."
    await update.message.reply_text(status_msg)


async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # We signal via kill file like before
    if _cfg:
        kf = _cfg.get("KILL_SWITCH_FILE", "/tmp/kill")
        open(kf, "a").close()
    await update.message.reply_text("⏸️ Paused monitoring.")


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _cfg:
        kf = _cfg.get("KILL_SWITCH_FILE", "/tmp/kill")
        if os.path.exists(kf):
            os.remove(kf)
    await update.message.reply_text("▶️ Resumed monitoring.")


async def kill_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _cfg:
        kf = _cfg.get("KILL_SWITCH_FILE", "/tmp/kill")
        open(kf, "a").close()
    await update.message.reply_text("🛑 Kill switch activated.")

# Upgrade TG (76-85): more commands
async def sell_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if len(args) < 1:
        await update.message.reply_text("Usage: /sell <token> [percent|all]  (e.g. /sell 0x... 50)")
        return
    token = args[0]
    pct = 100 if len(args) < 2 or args[1].lower() == "all" else float(args[1])
    await update.message.reply_text(f"🔔 Sell request for {pct}% of {token} received.")
    if _buy_callback and _current_w3 and _cfg:  # reuse for sell wiring
        # Note: for full, we'd have a sell_callback. For now trigger via main logic note.
        # In practice, user uses buttons or main bot for auto.
        print(f"[TG] Sell request logged for manual follow-up: {token} {pct}%")
    else:
        print("[TG] Sell context not available.")

async def positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Upgrade #77: real status from DB + ACTIVE
    try:
        import sqlite3
        conn = sqlite3.connect("/home/ubuntu/b20-bot/b20_trades.db")
        c = conn.cursor()
        c.execute("SELECT token, action, amount, status FROM trades ORDER BY id DESC LIMIT 5")
        rows = c.fetchall()
        conn.close()
        msg = "📊 Recent trades:\n" + "\n".join([f"{r[1]} {r[2]} {r[0][:8]}... {r[3]}" for r in rows]) if rows else "No trades yet."
    except:
        msg = "📊 Positions: DB read error or no trades. Use kill switch if needed."
    await update.message.reply_text(msg)

async def blacklist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Upgrade #81, #68
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /blacklist <token>")
        return
    token = args[0]
    # Note: this would need to be shared with main process; for now echo
    await update.message.reply_text(f"🖤 Blacklist request for {token} (add to main bot BLACKLIST set manually or extend wiring).")

async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Manual buy (upgrade #79)
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: /buy <token> <eth_amount>")
        return
    token, amt = args[0], float(args[1])
    await update.message.reply_text(f"🛒 Manual buy request: {amt} ETH for {token}. Triggering via bot context if available...")
    if _buy_callback and _current_w3 and _cfg:
        try:
            def _manual_buy():
                _buy_callback(_current_w3, token, 3000, amt, _cfg)
            threading.Thread(target=_manual_buy, daemon=True).start()
        except Exception as e:
            await update.message.reply_text(f"Error triggering buy: {e}")
    else:
        await update.message.reply_text("Buy context not fully wired yet. Use buttons or main bot.")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    chat_id = query.message.chat_id

    if data == "status":
        await query.edit_message_text("📊 Bot Status: LIVE mode active. Monitoring pools. Check logs.")
    elif data == "pause":
        if _cfg:
            kf = _cfg.get("KILL_SWITCH_FILE", "/tmp/kill")
            open(kf, "a").close()
        await query.edit_message_text("⏸️ Paused monitoring.")
    elif data == "resume":
        if _cfg:
            kf = _cfg.get("KILL_SWITCH_FILE", "/tmp/kill")
            if os.path.exists(kf):
                os.remove(kf)
        await query.edit_message_text("▶️ Resumed monitoring.")
    elif data == "kill":
        if _cfg:
            kf = _cfg.get("KILL_SWITCH_FILE", "/tmp/kill")
            open(kf, "a").close()
        await query.edit_message_text("🛑 Kill switch activated.")
    elif data.startswith("buy_"):
        try:
            _, tkn, amt_str = data.split("_", 2)
            amt = float(amt_str)

            use_w3 = _current_w3
            if use_w3 and _buy_callback and _cfg:
                # Send immediate ack via edit + fire the buy in background (like before)
                await query.edit_message_text(f"Executing buy {amt} ETH on {tkn}...")

                def _do_buy():
                    try:
                        f = 3000
                        # We let the main sniper's buy logic handle pool finding
                        _buy_callback(use_w3, tkn, f, amt, _cfg)
                    except Exception as be:
                        # The main code already does tg_send on errors
                        print(f"[TG BUY] background error: {be}")

                threading.Thread(target=_do_buy, daemon=True).start()
            else:
                await query.edit_message_text("Buy not available (no w3/cfg yet).")
        except Exception as e:
            await query.edit_message_text(f"Buy button error: {e}")


async def _post_init(application: Application):
    """Optional post init."""
    pass


def _build_application(token: str) -> Application:
    app = Application.builder().token(token).post_init(_post_init).build()

    # Commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("menu", start_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))
    app.add_handler(CommandHandler("kill", kill_cmd))
    app.add_handler(CommandHandler("sell", sell_cmd))
    app.add_handler(CommandHandler("positions", positions_cmd))
    app.add_handler(CommandHandler("blacklist", blacklist_cmd))
    app.add_handler(CommandHandler("buy", buy_cmd))

    # Inline buttons
    app.add_handler(CallbackQueryHandler(button_callback))

    return app


def start_telegram_bot_in_background() -> threading.Thread:
    """
    Starts the interactive bot using python-telegram-bot in a daemon thread.
    This replaces the old raw getUpdates long-poll thread.
    """
    token = _get_token()
    if not token:
        print("[TG] No token found, interactive bot disabled.")
        return None

    def _runner():
        async def _run():
            app = _build_application(token)
            # Clean webhook like ethbot diag
            try:
                await app.bot.delete_webhook(drop_pending_updates=True)
            except Exception:
                pass

            print("[TG] Starting python-telegram-bot polling (ethbot style)...")
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)

            # Keep the event loop alive
            try:
                await asyncio.Event().wait()
            finally:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()

        asyncio.run(_run())

    t = threading.Thread(target=_runner, daemon=True, name="tg-bot-poller")
    t.start()
    return t


# Convenience: expose a way to send control panel from main code if needed
def get_control_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("📊 Status", callback_data="status"),
            InlineKeyboardButton("⏸️ Pause", callback_data="pause"),
        ],
        [
            InlineKeyboardButton("▶️ Resume", callback_data="resume"),
            InlineKeyboardButton("🛑 Kill", callback_data="kill"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# For B20 detection buttons (called from main sniper)
# Returns python-telegram-bot object (if sending via app.bot)
def get_buy_keyboard(token_address: str) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("0.003 ETH", callback_data=f"buy_{token_address}_0.003"),
            InlineKeyboardButton("0.005 ETH", callback_data=f"buy_{token_address}_0.005"),
        ],
        [
            InlineKeyboardButton("0.007 ETH", callback_data=f"buy_{token_address}_0.007"),
            InlineKeyboardButton("0.01 ETH", callback_data=f"buy_{token_address}_0.01"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# Dict versions for use with simple requests tg_send (outbound alerts)
def get_buy_keyboard_dict(token_address: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "0.003 ETH", "callback_data": f"buy_{token_address}_0.003"},
                {"text": "0.005 ETH", "callback_data": f"buy_{token_address}_0.005"},
            ],
            [
                {"text": "0.007 ETH", "callback_data": f"buy_{token_address}_0.007"},
                {"text": "0.01 ETH", "callback_data": f"buy_{token_address}_0.01"},
            ],
        ]
    }


def get_control_keyboard_dict() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Status", "callback_data": "status"},
                {"text": "⏸️ Pause", "callback_data": "pause"},
            ],
            [
                {"text": "▶️ Resume", "callback_data": "resume"},
                {"text": "🛑 Kill", "callback_data": "kill"},
            ],
        ]
    }


if __name__ == "__main__":
    # Standalone test
    print("Starting standalone TG bot test...")
    start_telegram_bot_in_background()
    input("Press Enter to stop...\n")