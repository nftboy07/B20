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
from telegram.error import InvalidToken

try:
    from eth_utils import to_checksum_address
except Exception:
    def to_checksum_address(addr):
        return addr  # minimal fallback if eth-utils not resolved directly

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
_buy_callback: Optional[Callable] = None   # function(w3, token, fee, amount, cfg)
_sell_callback: Optional[Callable] = None  # function(w3, token, fee, amount_token, cfg)


def set_sniper_context(w3, cfg: dict, buy_callback: Callable, sell_callback: Callable = None):
    """Called by main sniper to inject context for buy/sell buttons etc."""
    global _current_w3, _cfg, _buy_callback, _sell_callback
    _current_w3 = w3
    _cfg = cfg
    _buy_callback = buy_callback
    _sell_callback = sell_callback or buy_callback  # fallback if needed


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
    await update.message.reply_text(
        "More commands (real mainnet):\n"
        "/status /positions /pnl /spent /value /open\n"
        "/ethbalance /balance <tok> /price <tok> /token <tok>\n"
        "/pools <tok> /safety <tok> /gas /config\n"
        "/history /tx <hash> /buy <tok> <amt> /sell <tok> <pct>\n"
        "/blacklistlist /export\n"
        "Buttons on detections + after buys. Use /tx to verify transfers."
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Enhanced status with real mainnet outputs: addr, ETH bal, buys, winrate, opens
    status_msg = "📊 Bot Status: LIVE mode active.\nMonitoring pools + B20Factory.\nTG interactive + buttons ready.\n"
    use_w3 = _current_w3
    try:
        from b20_mainnet_sniper import get_bot_address, get_open_positions, get_win_rate
        addr = get_bot_address()
        status_msg += f"Bot wallet: {addr}\n"
        if use_w3:
            try:
                sender = use_w3.eth.account.from_key(os.getenv("PRIVATE_KEY")).address
                bal_wei = use_w3.eth.get_balance(sender)
                bal_eth = bal_wei / 1e18
                status_msg += f"ETH Balance: {bal_eth:.6f} ETH\n"
            except Exception as be:
                status_msg += f"ETH Balance: error ({be})\n"
        opens = get_open_positions(use_w3)
        status_msg += f"Open positions (DB): {len(opens)}\n"
        wr = get_win_rate()
        status_msg += f"Win rate (from buys): {wr:.1f}%\n"
    except Exception as e:
        status_msg += f"(some real data unavailable: {e})\n"
    status_msg += "Use /positions /ethbalance /history /price <tok> for details."
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
    await update.message.reply_text(f"🔔 Sell request for {pct}% of {token} received. Computing real held balance...")
    use_w3 = _current_w3
    if _sell_callback and use_w3 and _cfg:
        def _do_sell():
            try:
                # Compute REAL held amount from on-chain, then pct of it
                sender = use_w3.eth.account.from_key(os.getenv("PRIVATE_KEY")).address
                erc = use_w3.eth.contract(address=to_checksum_address(token), abi=[
                    {"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},
                    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}
                ])
                held = erc.functions.balanceOf(sender).call()
                dec = erc.functions.decimals().call()
                sell_amt = int(held * (pct / 100.0)) if held > 0 else 0
                if sell_amt <= 0:
                    print("[TG SELL] zero held or computed amount, skipping")
                    return
                _sell_callback(use_w3, token, 3000, sell_amt, _cfg)
            except Exception as be:
                print(f"[TG SELL] background error: {be}")
        threading.Thread(target=_do_sell, daemon=True).start()
    else:
        print("[TG] Sell context not available.")

async def positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Real mainnet positions: DB spent + on-chain held balance + PnL using live price
    try:
        from b20_mainnet_sniper import get_open_positions
        use_w3 = _current_w3
        opens = get_open_positions(use_w3)
        sender = "N/A"
        try:
            if use_w3:
                sender = use_w3.eth.account.from_key(os.getenv("PRIVATE_KEY")).address
        except:
            pass
        if not opens:
            msg = "📊 No open positions yet (no successful buys in DB or zero held).\nBuy via buttons or /buy <tok> <eth>"
        else:
            msg = f"📊 OPEN POSITIONS (real on-chain + DB)\nWallet used for balances: {sender}\n\n"
            for p in opens:
                sym = p.get('symbol', '') or ''
                tshort = p['token'][:10] + "..."
                label = f"{sym} {tshort}" if sym else tshort
                msg += f"Token: {label}\n"
                msg += f"  Acquired at buy (DB): {p.get('acquired', 0):.8f}\n"
                msg += f"  Held (on-chain now): {p.get('held', 0):.8f}\n"
                msg += f"  ETH spent (DB): {p.get('eth_spent', 0):.6f}\n"
                ep = p.get('entry_price_eth', 0)
                if ep > 0:
                    msg += f"  Entry price: {ep:.10f} ETH per token\n"
                else:
                    msg += "  Entry price: N/A (0 acquired)\n"
                val = p.get('value_eth', 0)
                if val > 0:
                    msg += f"  Est Value: {val:.6f} ETH\n"
                    msg += f"  PnL: {p.get('pnl_eth',0):.6f} ETH ({p.get('pnl_pct',0):.1f}%)\n"
                else:
                    msg += "  Est Value: N/A (Quoter/price pending for fresh token)\n"
                    msg += "  PnL: N/A\n"
                msg += f"  Strategy: {p.get('suggestion','moon bag 30%')}\n"
                if p.get('note'):
                    msg += f"  ⚠️ {p['note']}\n\n"
                else:
                    msg += "\n"
            msg += "Moon bag = 30% hold for potential moon. Use /sell <tok> 25 or sell buttons.\n"
            msg += "Tip: /balance <tok> for exact held, /price <tok> for live quote.\n"
            msg += "To see profit: if Held >0 and Value > Spent → in profit. Check /tx for buy tx 'From' vs this wallet."
            if any(p.get('held',0) == 0 and p.get('eth_spent',0) > 0 for p in opens):
                msg += "\n⚠️ Some held=0 but spent recorded: check wallet matches buy tx 'from', or high tax/burn on token."
            msg += "\nNote: Acquired=0 means the swap delivered 0 tokens to wallet (tax, liq, or redirect). No profit possible on those."
    except Exception as e:
        msg = f"📊 Positions error: {e}"
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

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Real mainnet balance: /balance [<token>]  (no arg = ETH balance)
    args = context.args or []
    use_w3 = _current_w3
    if not use_w3:
        await update.message.reply_text("No w3 context.")
        return
    try:
        sender = use_w3.eth.account.from_key(os.getenv("PRIVATE_KEY")).address
        if not args:
            # ETH balance
            bal_wei = use_w3.eth.get_balance(sender)
            await update.message.reply_text(f"Bot ETH balance: {bal_wei / 1e18:.6f} ETH\nWallet: {sender}")
            return
        token = args[0]
        erc = use_w3.eth.contract(address=to_checksum_address(token), abi=[
            {"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},
            {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
            {"constant":True,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"}
        ])
        bal = erc.functions.balanceOf(sender).call()
        dec = erc.functions.decimals().call()
        try:
            sym = erc.functions.symbol().call()
        except:
            sym = ""
        human = bal / (10 ** dec) if dec else bal
        await update.message.reply_text(f"Balance {sym} {token[:10]}... : {human}\nRaw: {bal} (dec={dec})\nWallet: {sender}")
    except Exception as e:
        await update.message.reply_text(f"Balance error: {e}")

async def address_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot's wallet address (the one used for positions and balances)."""
    use_w3 = _current_w3
    if use_w3:
        try:
            sender = use_w3.eth.account.from_key(os.getenv("PRIVATE_KEY")).address
            await update.message.reply_text(f"Bot wallet: {sender}\n(This is used for /positions and /balance queries. Make sure it matches your buy tx 'from'.)")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
    else:
        await update.message.reply_text("No w3 context.")

async def price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Real mainnet price (QuoterV2 or slot0) in ETH per token."""
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /price <token>")
        return
    token = args[0]
    use_w3 = _current_w3
    if use_w3:
        try:
            from b20_mainnet_sniper import get_token_price_in_eth, get_token_decimals
            price = get_token_price_in_eth(use_w3, token)
            dec = get_token_decimals(use_w3, token)
            if price > 0:
                await update.message.reply_text(f"💰 Price {token[:10]}... : {price:.10f} ETH per token\n(dec={dec})")
            else:
                await update.message.reply_text(f"Price {token[:10]}... : {price} ETH (0 = no liq/Quoter yet or very new pool)\n(dec={dec})\nTry again after more buys or use /pools")
        except Exception as e:
            await update.message.reply_text(f"Price error: {e}")
    else:
        await update.message.reply_text("No w3 context.")

async def token_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Real mainnet token info."""
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /token <token>")
        return
    token = args[0]
    use_w3 = _current_w3
    if use_w3:
        try:
            erc = use_w3.eth.contract(address=to_checksum_address(token), abi=[
                {"constant":True,"inputs":[],"name":"name","outputs":[{"name":"","type":"string"}],"type":"function"},
                {"constant":True,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"},
                {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
                {"constant":True,"inputs":[],"name":"totalSupply","outputs":[{"name":"","type":"uint256"}],"type":"function"},
            ])
            name = erc.functions.name().call()
            sym = erc.functions.symbol().call()
            dec = erc.functions.decimals().call()
            supply = erc.functions.totalSupply().call()
            await update.message.reply_text(f"Token {token}\nName: {name}\nSymbol: {sym}\nDecimals: {dec}\nTotalSupply: {supply / (10**dec)}")
        except Exception as e:
            await update.message.reply_text(f"Token info error: {e}")
    else:
        await update.message.reply_text("No w3 context.")


async def ethbalance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Real mainnet ETH balance of the bot wallet."""
    use_w3 = _current_w3
    if not use_w3:
        await update.message.reply_text("No w3 context.")
        return
    try:
        pk = os.getenv("PRIVATE_KEY")
        sender = use_w3.eth.account.from_key(pk).address if pk else "N/A"
        bal_wei = use_w3.eth.get_balance(sender)
        bal = bal_wei / 1e18
        await update.message.reply_text(f"💎 Bot ETH Balance (mainnet):\n{bal:.8f} ETH\nAddress: {sender}")
    except Exception as e:
        await update.message.reply_text(f"ETH balance error: {e}")


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recent trades from DB with real mainnet tx data."""
    args = context.args or []
    limit = 5
    if args:
        try: limit = max(1, min(20, int(args[0])))
        except: pass
    try:
        import sqlite3
        db_candidates = ["b20_trades.db", "/home/ubuntu/b20-bot/b20_trades.db"]
        rows = []
        for dbp in db_candidates:
            try:
                conn = sqlite3.connect(dbp)
                c = conn.cursor()
                c.execute("SELECT timestamp, token, action, amount, tx_hash, status FROM trades ORDER BY id DESC LIMIT ?", (limit,))
                rows = c.fetchall()
                conn.close()
                if rows: break
            except:
                continue
        if not rows:
            await update.message.reply_text("No trade history in DB yet.")
            return
        msg = f"📜 Last {len(rows)} trades (mainnet):\n\n"
        for ts, tok, act, amt, txh, st in rows:
            short_tok = (tok or "")[:10] + "..."
            short_tx = (txh or "")[:10] + "..." if txh else "N/A"
            msg += f"{ts[:19]} {act} {short_tok} amt={amt} {st}\ntx: {short_tx}\n\n"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"History error: {e}")


async def pools_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show real mainnet pools + liquidity for a token (multiple fees)."""
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /pools <token>")
        return
    token = args[0]
    use_w3 = _current_w3
    if not use_w3:
        await update.message.reply_text("No w3 context.")
        return
    try:
        from b20_mainnet_sniper import find_or_wait_pool, check_pool_liquidity, WETH
        fees = [500, 3000, 10000]
        lines = [f"Pools for {token} (WETH pairs):"]
        for fee in fees:
            pool = find_or_wait_pool(use_w3, WETH, token, fee) or find_or_wait_pool(use_w3, token, WETH, fee)
            if pool:
                liq = check_pool_liquidity(use_w3, pool)
                liq_eth = liq / 1e18 if liq else 0
                lines.append(f"  fee={fee}: {pool}  liq≈{liq_eth:.4f}")
            else:
                lines.append(f"  fee={fee}: no pool")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Pools error: {e}")


async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Real mainnet tx details + receipt (for any tx hash)."""
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /tx <0xhash>")
        return
    txh = args[0]
    use_w3 = _current_w3
    if not use_w3:
        await update.message.reply_text("No w3 context.")
        return
    try:
        receipt = use_w3.eth.get_transaction_receipt(txh)
        tx = use_w3.eth.get_transaction(txh)
        status = "SUCCESS" if receipt.status == 1 else "FAILED"
        gas_used = receipt.gasUsed
        block = receipt.blockNumber
        from_a = tx.get("from", "N/A")
        to_a = tx.get("to", "N/A")
        val = tx.get("value", 0) / 1e18
        msg = (f"Tx {txh}\nStatus: {status}\nBlock: {block}\n"
               f"From: {from_a}\nTo: {to_a}\nValue: {val} ETH\n"
               f"Gas used: {gas_used}\n"
               f"Basescan: https://basescan.org/tx/{txh}")
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Tx lookup error: {e} (may be pending or not indexed)")


async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export trades CSV (real DB data)."""
    try:
        from b20_mainnet_sniper import export_trades_csv
        ok = export_trades_csv("tg_export_trades.csv")
        await update.message.reply_text("CSV exported to tg_export_trades.csv" if ok else "Export failed (see logs).")
    except Exception as e:
        await update.message.reply_text(f"Export error: {e}")


async def pnl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Overall real mainnet PnL summary."""
    use_w3 = _current_w3
    try:
        from b20_mainnet_sniper import get_total_spent, get_estimated_portfolio_value, get_open_positions, get_win_rate
        spent = get_total_spent()
        value = get_estimated_portfolio_value(use_w3)
        pnl = value - spent
        opens = get_open_positions(use_w3)
        wr = get_win_rate()
        msg = (f"📈 PNL SUMMARY (mainnet)\n"
               f"Total ETH spent (buys): {spent:.6f}\n"
               f"Est. portfolio value: {value:.6f}\n"
               f"Overall PnL: {pnl:.6f} ETH\n"
               f"Open positions: {len(opens)}\n"
               f"Win rate: {wr:.1f}%\n"
               f"Wallet: {use_w3.eth.account.from_key(os.getenv('PRIVATE_KEY')).address if use_w3 else 'N/A'}")
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"PnL error: {e}")


async def spent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Total real ETH spent on buys."""
    try:
        from b20_mainnet_sniper import get_total_spent
        spent = get_total_spent()
        await update.message.reply_text(f"💸 Total ETH spent on successful buys: {spent:.6f} ETH")
    except Exception as e:
        await update.message.reply_text(f"Spent error: {e}")


async def value_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Current estimated portfolio value from live prices."""
    use_w3 = _current_w3
    try:
        from b20_mainnet_sniper import get_estimated_portfolio_value
        val = get_estimated_portfolio_value(use_w3)
        await update.message.reply_text(f"💰 Est. portfolio value (held × price): {val:.6f} ETH")
    except Exception as e:
        await update.message.reply_text(f"Value error: {e}")


async def gas_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Real mainnet gas prices."""
    use_w3 = _current_w3
    if not use_w3:
        await update.message.reply_text("No w3 context.")
        return
    try:
        from b20_mainnet_sniper import get_gas_info
        g = get_gas_info(use_w3)
        await update.message.reply_text(f"⛽ Gas: {g.get('gas_price_gwei')} gwei | Base fee: {g.get('base_fee_gwei')} gwei")
    except Exception as e:
        await update.message.reply_text(f"Gas error: {e}")


async def safety_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run safety checks on a token (real on-chain)."""
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /safety <token>")
        return
    token = args[0]
    use_w3 = _current_w3
    if not use_w3:
        await update.message.reply_text("No w3 context.")
        return
    try:
        from b20_mainnet_sniper import run_token_safety
        res = run_token_safety(use_w3, token)
        await update.message.reply_text(f"🛡️ {res}")
    except Exception as e:
        await update.message.reply_text(f"Safety error: {e}")


async def config_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current bot config (real values, no secrets)."""
    if _cfg:
        safe = {k: v for k, v in _cfg.items() if not any(x in k for x in ['KEY', 'TOKEN', 'RPC'])}
        msg = "⚙️ Current config:\n" + "\n".join(f"{k}: {v}" for k, v in safe.items())
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("No config loaded.")


async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick list of open position tokens (real DB)."""
    try:
        from b20_mainnet_sniper import get_open_positions
        opens = get_open_positions(_current_w3)
        if not opens:
            await update.message.reply_text("No open positions.")
            return
        lines = ["📋 Open tokens:"]
        for p in opens:
            lines.append(f"- {p.get('symbol','')} {p['token'][:8]}... spent:{p['eth_spent']:.4f} held:{p['held']:.2f}")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Open error: {e}")


async def blacklistlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List current blacklist."""
    try:
        from b20_mainnet_sniper import BLACKLIST
        if BLACKLIST:
            await update.message.reply_text("🖤 Blacklist: " + ", ".join(list(BLACKLIST)[:5]))
        else:
            await update.message.reply_text("Blacklist is empty.")
    except Exception as e:
        await update.message.reply_text(f"Blacklist error: {e}")


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
                        # Let attempt_buy find best fee/pool (improved)
                        _buy_callback(use_w3, tkn, 3000, amt, _cfg)
                    except Exception as be:
                        # The main code already does tg_send on errors
                        print(f"[TG BUY] background error: {be}")

                threading.Thread(target=_do_buy, daemon=True).start()
            else:
                await query.edit_message_text("Buy not available (no w3/cfg yet).")
        except Exception as e:
            await query.edit_message_text(f"Buy button error: {e}")
    elif data.startswith("sell_"):
        try:
            _, tkn, pct_str = data.split("_", 2)
            pct = float(pct_str)
            use_w3 = _current_w3
            if use_w3 and _sell_callback and _cfg:
                await query.edit_message_text(f"Executing sell {pct}% of {tkn} (real balance)...")
                def _do_sell():
                    try:
                        # Real held amount from on-chain for accurate %
                        sender = use_w3.eth.account.from_key(os.getenv("PRIVATE_KEY")).address
                        erc = use_w3.eth.contract(address=to_checksum_address(tkn), abi=[
                            {"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},
                            {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}
                        ])
                        held = erc.functions.balanceOf(sender).call()
                        dec = erc.functions.decimals().call()
                        sell_amt = int(held * (pct / 100.0)) if held > 0 else 0
                        if sell_amt > 0:
                            _sell_callback(use_w3, tkn, 3000, sell_amt, _cfg)
                        else:
                            print("[TG SELL BTN] computed sell_amt=0")
                    except Exception as be:
                        print(f"[TG SELL] background error: {be}")
                threading.Thread(target=_do_sell, daemon=True).start()
            else:
                await query.edit_message_text("Sell not available.")
        except Exception as e:
            await query.edit_message_text(f"Sell button error: {e}")


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
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("ethbalance", ethbalance_cmd))
    app.add_handler(CommandHandler("eth", ethbalance_cmd))
    app.add_handler(CommandHandler("wallet", ethbalance_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("trades", history_cmd))
    app.add_handler(CommandHandler("pools", pools_cmd))
    app.add_handler(CommandHandler("tx", tx_cmd))
    app.add_handler(CommandHandler("tokeninfo", token_cmd))
    app.add_handler(CommandHandler("help", start_cmd))
    app.add_handler(CommandHandler("commands", start_cmd))
    app.add_handler(CommandHandler("export", export_cmd))
    app.add_handler(CommandHandler("csv", export_cmd))
    app.add_handler(CommandHandler("pnl", pnl_cmd))
    app.add_handler(CommandHandler("spent", spent_cmd))
    app.add_handler(CommandHandler("value", value_cmd))
    app.add_handler(CommandHandler("gas", gas_cmd))
    app.add_handler(CommandHandler("safety", safety_cmd))
    app.add_handler(CommandHandler("config", config_cmd))
    app.add_handler(CommandHandler("open", open_cmd))
    app.add_handler(CommandHandler("blacklistlist", blacklistlist_cmd))

    # Inline buttons
    app.add_handler(CallbackQueryHandler(button_callback))

    return app


def start_telegram_bot_in_background() -> threading.Thread:
    """
    Starts the interactive bot using python-telegram-bot in a daemon thread.
    This replaces the old raw getUpdates long-poll thread.
    Gracefully handles invalid token (e.g. example placeholder).
    """
    token = _get_token()
    if not token:
        print("[TG] No token found, interactive bot disabled.")
        return None

    def _runner():
        async def _run():
            try:
                app = _build_application(token)
            except InvalidToken as e:
                print(f"[TG] Invalid token (replace example with real from @BotFather): {e}")
                return
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

def get_sell_keyboard_dict(token_address: str) -> dict:
    """Sell buttons for a bought token (upgrade for TP)."""
    return {
        "inline_keyboard": [
            [
                {"text": "Sell 25%", "callback_data": f"sell_{token_address}_25"},
                {"text": "Sell 50%", "callback_data": f"sell_{token_address}_50"},
            ],
            [
                {"text": "Sell 100%", "callback_data": f"sell_{token_address}_100"},
            ],
        ]
    }


if __name__ == "__main__":
    # Standalone test
    print("Starting standalone TG bot test...")
    start_telegram_bot_in_background()
    input("Press Enter to stop...\n")