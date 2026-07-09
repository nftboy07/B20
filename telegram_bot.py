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

import time

# Short TTL cache to speed up repeated menu button presses while keeping data fresh/real
_cache = {}
_CACHE_TTL = 8  # seconds

def _get_cached(key):
    entry = _cache.get(key)
    if entry and (time.time() - entry[0] < _CACHE_TTL):
        return entry[1]
    return None

def _set_cached(key, value):
    _cache[key] = (time.time(), value)


async def _send_control_panel(update_or_chat, context: ContextTypes.DEFAULT_TYPE = None):
    """Show the rich main menu with buttons for almost all commands.
    All button outputs use real mainnet data (on-chain calls, real DB, Quoter etc).
    """
    reply_markup = get_main_menu_keyboard()
    text = "B20 Bot Main Menu (LIVE - All data is REAL mainnet):"

    if hasattr(update_or_chat, "message"):
        # from command
        await update_or_chat.message.reply_text(text, reply_markup=reply_markup)
    else:
        # from callback or direct
        chat_id = update_or_chat
        pass  # handled in caller for now


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_control_panel(update, context)
    await help_cmd(update, context)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comprehensive command list."""
    help_text = """📋 B20 Sniper - All Commands (real mainnet)

**Control**
/status /pause /resume /kill /refresh

**Trading**
/buy <token> <eth>    /sell <token> [25|50|100|all]
/positions

**Balances & Info (live on-chain)**
/balance <token>   /ethbalance /wallet
/price <token>     /token <token>
/liq <token>       /pools <token>
/gas               /simulate <token> <eth>
/safety <token>    /perftoken <token>
/activation        /rpc

**Analytics & History**
/pnl /spent /value /summary /stats /profit <token>
/history /recent [n] /lastbuy /open
/tx <hash>         /export /csv

**More**
/balance /price /pools /tx /buy /sell /blacklist /addblack /remblack /liq /simulate /safety /perftoken /pools /activation /rpc /gas /config /refresh

**Management**
/blacklist <token>   /blacklistlist
/addblack <token>    /remblack <token>
/config

**Help**
/help /list /commands /menu

Buttons appear on new detections + after buys.
Use /tx to inspect any transaction on Basescan.

You can always sell (buttons or /sell) even if /positions shows 0/N/A — the sell logic uses live on-chain balance at click time.
Use /profit <token> or /balance + /price to check profitability yourself."""

    await update.message.reply_text(help_text)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Enhanced status with real mainnet outputs: addr, ETH bal, buys, winrate, opens
    status_msg = "📊 Bot Status: LIVE mode active.\nMonitoring pools + B20Factory.\nTG interactive + buttons ready.\n"
    use_w3 = _current_w3
    try:
        from b20_mainnet_sniper import get_bot_address, get_open_positions, get_win_rate
        addr = get_bot_address()
        status_msg += f"Bot wallet: {addr}\n"
        if use_w3:
            def _get_bal():
                try:
                    sender = use_w3.eth.account.from_key(os.getenv("PRIVATE_KEY")).address
                    bal_wei = use_w3.eth.get_balance(sender)
                    return bal_wei / 1e18
                except Exception as be:
                    return 0.0
            loop = asyncio.get_running_loop()
            bal_eth = await loop.run_in_executor(None, _get_bal)
            status_msg += f"ETH Balance: {bal_eth:.6f} ETH\n"
        def _get_opens_len():
            opens = get_open_positions(use_w3)
            return len(opens)
        loop = asyncio.get_running_loop()
        opens_len = await loop.run_in_executor(None, _get_opens_len)
        status_msg += f"Open positions (DB): {opens_len}\n"
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
            msg = "📊 No open positions yet (no successful buys in DB).\nBuy via buttons or /buy <tok> <eth>"
        else:
            msg = f"📊 OPEN POSITIONS (REAL on-chain + DB)\nWallet: {sender}\n\n"
            realized_loss = 0.0
            for p in opens:
                sym = p.get('symbol', '') or ''
                tshort = p['token'][:10] + "..."
                label = f"{sym} {tshort}" if sym else tshort
                acq = p.get('acquired', 0)
                held = p.get('held', 0)
                spent = p.get('eth_spent', 0)
                ep = p.get('entry_price_eth', 0)
                val = p.get('value_eth', 0)
                msg += f"Token: {label}\n"
                msg += f"  Acquired (at buy): {acq:.8f}\n"
                msg += f"  Held (live): {held:.8f}\n"
                msg += f"  Spent: {spent:.6f} ETH\n"
                if ep > 0:
                    msg += f"  Entry price: {ep:.10f} ETH/token\n"
                if val > 0:
                    msg += f"  Value: {val:.6f} PnL: {p.get('pnl_eth',0):.6f} ({p.get('pnl_pct',0):.1f}%)\n"
                else:
                    msg += "  Value/PnL: N/A (price pending)\n"
                if acq == 0 and spent > 0:
                    realized_loss += spent
                    msg += "  ⚠️ 0 acquired - likely tax/redirect. Realized loss.\n"
                msg += f"  Strategy: {p.get('suggestion','moon bag 30%')}\n\n"
            if realized_loss > 0:
                msg += f"Total realized loss (0-received): -{realized_loss:.6f} ETH\n"
            msg += "Moon bag = 30% hold. Use sell buttons or /sell.\n"
            msg += "Check profit: /profit <tok> or /balance + /price.\n"
            msg += "Sell anytime - buttons use live balance, not this view."
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


async def recent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Last few buys with real acquired data."""
    args = context.args or []
    n = 5
    if args:
        try: n = max(1, min(10, int(args[0])))
        except: pass
    try:
        import sqlite3
        conn = sqlite3.connect("b20_trades.db")
        c = conn.cursor()
        c.execute("SELECT timestamp, token, amount, tx_hash, status, COALESCE(token_amount,0) FROM trades WHERE action='buy' ORDER BY id DESC LIMIT ?", (n,))
        rows = c.fetchall()
        conn.close()
        if not rows:
            await update.message.reply_text("No recent buys.")
            return
        msg = f"📜 Last {len(rows)} buys:\n"
        for ts, tok, amt, txh, st, acquired in rows:
            short = tok[:8] + "..." if tok else "?"
            msg += f"{ts[:16]} {short} spent={amt} acq={acquired:.2f} {st}\nTx: {txh[:10]}...\n"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Recent error: {e}")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detailed stats."""
    try:
        from b20_mainnet_sniper import get_detailed_stats, get_total_spent, get_win_rate
        st = get_detailed_stats()
        wr = get_win_rate()
        msg = (f"📊 STATS\n"
               f"Successful buys: {st['successful_buys']}\n"
               f"Total spent: {st['total_spent']:.4f} ETH\n"
               f"Sells: {st['sells']}\n"
               f"Win rate: {wr:.1f}%\n"
               f"Buys logged: {st['total_buys_logged']}")
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Stats error: {e}")


async def activation_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """B20 activation status (real on-chain)."""
    use_w3 = _current_w3
    if not use_w3:
        await update.message.reply_text("No w3.")
        return
    try:
        from b20_mainnet_sniper import get_activation_status
        res = get_activation_status(use_w3)
        await update.message.reply_text(f"🔓 {res}")
    except Exception as e:
        await update.message.reply_text(f"Activation error: {e}")


async def rpc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Current RPC status."""
    if _cfg:
        rpc = _cfg.get('RPC_URL', 'N/A')
        backups = len(_cfg.get('BACKUP_RPCS', []))
        await update.message.reply_text(f"🌐 RPC: {rpc[:40]}...\nBackups configured: {backups}")
    else:
        await update.message.reply_text("No cfg.")


async def perftoken_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """PnL / details for specific token."""
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /perftoken <token>")
        return
    token = args[0]
    use_w3 = _current_w3
    try:
        from b20_mainnet_sniper import get_open_positions, get_token_price_in_eth
        opens = [p for p in get_open_positions(use_w3) if p['token'].lower() == token.lower() or p.get('symbol','').lower() == token.lower()]
        if not opens:
            await update.message.reply_text("No position for that token.")
            return
        p = opens[0]
        price = get_token_price_in_eth(use_w3, p['token']) if use_w3 else 0
        val = p['held'] * price
        pnl = val - p['eth_spent']
        msg = (f"Token: {p['token'][:10]}...\n"
               f"Held: {p['held']:.4f} Acquired: {p['acquired']:.4f}\n"
               f"Spent: {p['eth_spent']:.4f}\n"
               f"Current price: {price:.10f}\n"
               f"Value: {val:.6f} PnL: {pnl:.6f}")
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Perf error: {e}")


async def refresh_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force refresh positions data (re-query balances)."""
    try:
        from b20_mainnet_sniper import get_open_positions
        opens = get_open_positions(_current_w3)
        await update.message.reply_text(f"Refreshed. {len(opens)} positions loaded.")
    except Exception as e:
        await update.message.reply_text(f"Refresh error: {e}")


async def liq_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Liquidity for a token (real mainnet)."""
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /liq <token>")
        return
    token = args[0]
    use_w3 = _current_w3
    if not use_w3:
        await update.message.reply_text("No w3 context.")
        return
    try:
        from b20_mainnet_sniper import find_best_pool, check_pool_liquidity, get_token_price_in_eth
        pool, fee = find_best_pool(use_w3, token)
        if pool:
            liq = check_pool_liquidity(use_w3, pool)
            price = get_token_price_in_eth(use_w3, token)
            await update.message.reply_text(f"💧 Liq for {token[:8]}... : {liq/1e18:.4f} ETH (fee {fee}) price~{price:.10f}")
        else:
            await update.message.reply_text("No pool found.")
    except Exception as e:
        await update.message.reply_text(f"Liq error: {e}")


async def simulate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Simulate buy output (Quoter, real mainnet, no tx)."""
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: /simulate <token> <eth>")
        return
    token, amt = args[0], float(args[1])
    use_w3 = _current_w3
    if not use_w3:
        await update.message.reply_text("No w3.")
        return
    try:
        from b20_mainnet_sniper import get_accurate_min_out, find_best_pool
        pool, fee = find_best_pool(use_w3, token)
        if not fee: fee = 3000
        amount_in = use_w3.to_wei(amt, 'ether')
        min_out = get_accurate_min_out(use_w3, token, fee, amount_in, 2000)
        dec = 18  # assume
        try:
            from b20_mainnet_sniper import get_token_decimals
            dec = get_token_decimals(use_w3, token)
        except:
            pass
        await update.message.reply_text(f"🧪 Simulate {amt} ETH -> ~{min_out / (10**dec):.4f} tokens (min, fee {fee})")
    except Exception as e:
        await update.message.reply_text(f"Sim error: {e}")


async def addblack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add token to blacklist."""
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /addblack <token>")
        return
    token = args[0]
    try:
        from b20_mainnet_sniper import BLACKLIST
        BLACKLIST.add(token.lower())
        await update.message.reply_text(f"🖤 Added {token[:8]}... to blacklist.")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def remblack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove from blacklist."""
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /remblack <token>")
        return
    token = args[0]
    try:
        from b20_mainnet_sniper import BLACKLIST
        BLACKLIST.discard(token.lower())
        await update.message.reply_text(f"Removed {token[:8]}... from blacklist.")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def lastbuy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Details of last buy."""
    try:
        import sqlite3
        conn = sqlite3.connect("b20_trades.db")
        c = conn.cursor()
        c.execute("SELECT timestamp, token, amount, tx_hash, status, COALESCE(token_amount,0) FROM trades WHERE action='buy' ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        conn.close()
        if not row:
            await update.message.reply_text("No buys yet.")
            return
        ts, tok, amt, txh, st, acq = row
        await update.message.reply_text(f"🛒 Last buy: {ts}\nToken: {tok}\nSpent: {amt} ETH\nAcquired: {acq}\nStatus: {st}\nTx: {txh}\nBasescan: https://basescan.org/tx/{txh}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick summary of bot state."""
    use_w3 = _current_w3
    try:
        from b20_mainnet_sniper import get_total_spent, get_estimated_portfolio_value, get_open_positions, get_win_rate, get_bot_address
        def _compute_sum():
            spent = get_total_spent()
            val = get_estimated_portfolio_value(use_w3)
            opens = len(get_open_positions(use_w3))
            wr = get_win_rate()
            addr = get_bot_address()
            eth = 0
            if use_w3:
                try:
                    sender = use_w3.eth.account.from_key(os.getenv("PRIVATE_KEY")).address
                    eth = use_w3.eth.get_balance(sender) / 1e18
                except: pass
            return f"📋 SUMMARY\nWallet: {addr}\nETH bal: {eth:.4f}\nSpent: {spent:.4f}\nValue: {val:.4f}\nOpens: {opens}\nWinrate: {wr:.1f}%"
        loop = asyncio.get_running_loop()
        msg = await loop.run_in_executor(None, _compute_sum)
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def profit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if a specific token is profitable right now using live balance + price vs DB spent.
    This lets you decide to sell even if /positions shows N/A or 0s."""
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /profit <token>   (e.g. /profit 0x2a41...)")
        return
    token = args[0]
    use_w3 = _current_w3
    if not use_w3:
        await update.message.reply_text("No w3 context.")
        return
    try:
        from b20_mainnet_sniper import get_token_price_in_eth
        def _compute_profit():
            # live held
            erc = use_w3.eth.contract(address=to_checksum_address(token), abi=[
                {"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},
                {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}
            ])
            sender = use_w3.eth.account.from_key(os.getenv("PRIVATE_KEY")).address
            held = erc.functions.balanceOf(sender).call()
            dec = erc.functions.decimals().call()
            held_human = held / (10 ** dec) if dec else 0

            price = get_token_price_in_eth(use_w3, token)
            value = held_human * price

            # spent from DB for this token
            import sqlite3
            spent = 0.0
            conn = sqlite3.connect("b20_trades.db")
            c = conn.cursor()
            c.execute("SELECT SUM(amount) FROM trades WHERE lower(token)=lower(?) AND action='buy' AND status='success'", (token,))
            row = c.fetchone()
            if row and row[0]:
                spent = row[0]
            conn.close()

            pnl = value - spent
            pct = (pnl / spent * 100) if spent > 0 else 0

            msg = (f"💰 Profit check for {token[:10]}...\n"
                   f"Held now (live): {held_human:.8f}\n"
                   f"Current price: {price:.10f} ETH\n"
                   f"Current value: {value:.6f} ETH\n"
                   f"ETH spent on buys (DB): {spent:.6f}\n"
                   f"PnL: {pnl:.6f} ETH ({pct:.1f}%)\n\n")
            if held_human > 0:
                if pnl > 0:
                    msg += "✅ Looks profitable right now based on live price."
                else:
                    msg += "❌ Currently at a loss (or break-even) based on live price."
            else:
                msg += "You currently hold 0 of this token on-chain."
            return msg
        loop = asyncio.get_running_loop()
        msg = await loop.run_in_executor(None, _compute_profit)
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Profit check error: {e}")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    chat_id = query.message.chat_id

    # Immediate feedback for heavy data-fetching buttons to prevent lag feel
    heavy_data_cmds = {"cmd_status", "status", "cmd_pnl", "cmd_value", "cmd_summary", "cmd_positions", "cmd_liq", "cmd_simulate", "cmd_safety", "cmd_perftoken", "cmd_stats", "cmd_history", "cmd_recent", "cmd_open"}
    if data in heavy_data_cmds:
        try:
            await query.edit_message_text("⏳ Fetching real mainnet data...")
        except Exception:
            pass

    # For very heavy buttons, offload computation to not block
    if data in {"cmd_positions", "cmd_pnl", "cmd_summary"}:
        # the rest of computation happens below; this is just note
        pass

    if data == "cmd_status" or data == "status":
        # REAL status output - offload blocking web3
        status_msg = "📊 Bot Status: LIVE mode active.\nMonitoring pools + B20Factory.\nTG interactive + buttons ready.\n"
        use_w3 = _current_w3
        try:
            from b20_mainnet_sniper import get_bot_address, get_num_open_positions, get_win_rate
            addr = get_bot_address()
            status_msg += f"Bot wallet: {addr}\n"
            if use_w3:
                def _get_bal():
                    try:
                        sender = use_w3.eth.account.from_key(os.getenv("PRIVATE_KEY")).address
                        bal_wei = use_w3.eth.get_balance(sender)
                        return bal_wei / 1e18
                    except:
                        return 0.0
                loop = asyncio.get_running_loop()
                bal_eth = await loop.run_in_executor(None, _get_bal)
                status_msg += f"ETH Balance: {bal_eth:.6f} ETH\n"
            n = get_num_open_positions()
            status_msg += f"Open positions: {n}\n"
            wr = get_win_rate()
            status_msg += f"Win rate: {wr:.1f}%\n"
        except Exception as e:
            status_msg += f"(some data error: {e})\n"
        status_msg += "Use /positions /pnl etc for details. All data is LIVE mainnet."
        await query.message.reply_text(status_msg)

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

    elif data == "cmd_pnl":
        use_w3 = _current_w3
        try:
            cached = _get_cached('pnl')
            if cached:
                await query.message.reply_text(cached)
                return
            from b20_mainnet_sniper import get_total_spent, get_estimated_portfolio_value, get_open_positions, get_win_rate
            def _compute_pnl():
                spent = get_total_spent()
                value = get_estimated_portfolio_value(use_w3)
                # calculate realized losses from 0-received
                opens = get_open_positions(use_w3)
                realized_loss = sum(p.get('eth_spent', 0) for p in opens if p.get('acquired', 0) == 0 and p.get('eth_spent', 0) > 0)
                pnl = value - spent + realized_loss  # since realized is loss, but spent already includes, adjust? Wait, spent is total, value is current, realized loss is part of it
                # better: total spent on open + realized = total out, current value - (total spent - realized? ) wait, simplify
                effective_pnl = value - (spent - realized_loss)  # value from good positions - spent on good
                # but to keep simple, show total spent, value, realized loss, effective
                msg = (f"📈 PNL SUMMARY (REAL mainnet)\n"
                       f"Total ETH spent: {spent:.6f}\n"
                       f"Est. value (held positions): {value:.6f}\n"
                       f"Realized loss (0-received buys): -{realized_loss:.6f}\n"
                       f"Effective PnL on active: {effective_pnl:.6f} ETH\n"
                       f"Open positions (with tokens): {len([p for p in opens if p.get('acquired',0)>0 or p.get('held',0)>0])}\n"
                       f"Win rate: {wr:.1f}%")
                return msg
            loop = asyncio.get_running_loop()
            msg = await loop.run_in_executor(None, _compute_pnl)
            _set_cached('pnl', msg)
            await query.message.reply_text(msg)
        except Exception as e:
            await query.message.reply_text(f"PnL error: {e}")

    elif data == "cmd_value":
        use_w3 = _current_w3
        try:
            cached = _get_cached('portfolio_value')
            if cached is not None:
                await query.message.reply_text(f"💰 REAL Est. portfolio value: {cached:.6f} ETH")
                return
            from b20_mainnet_sniper import get_estimated_portfolio_value
            def _compute_value():
                return get_estimated_portfolio_value(use_w3)
            loop = asyncio.get_running_loop()
            val = await loop.run_in_executor(None, _compute_value)
            _set_cached('portfolio_value', val)
            await query.message.reply_text(f"💰 REAL Est. portfolio value: {val:.6f} ETH")
        except Exception as e:
            await query.message.reply_text(f"Value error: {e}")

    elif data == "cmd_spent":
        try:
            from b20_mainnet_sniper import get_total_spent
            spent = get_total_spent()
            await query.message.reply_text(f"💸 REAL Total ETH spent: {spent:.6f} ETH")
        except Exception as e:
            await query.message.reply_text(f"Spent error: {e}")

    elif data == "cmd_summary":
        use_w3 = _current_w3
        try:
            cached_val = _get_cached('portfolio_value')
            cached_spent = _get_cached('spent')
            if cached_val is not None and cached_spent is not None:
                # use cached for fast summary
                from b20_mainnet_sniper import get_num_open_positions, get_win_rate, get_bot_address
                spent = cached_spent
                val = cached_val
                opens = get_num_open_positions()
                wr = get_win_rate()
                addr = get_bot_address()
                eth = 0.0
                if use_w3:
                    try:
                        sender = use_w3.eth.account.from_key(os.getenv("PRIVATE_KEY")).address
                        eth = use_w3.eth.get_balance(sender) / 1e18
                    except: pass
                msg = f"📋 SUMMARY (REAL, cached)\nWallet: {addr}\nETH bal: {eth:.4f}\nSpent: {spent:.4f}\nValue: {val:.4f}\nOpens: {opens}\nWinrate: {wr:.1f}%"
                await query.message.reply_text(msg)
                return
            from b20_mainnet_sniper import get_total_spent, get_estimated_portfolio_value, get_num_open_positions, get_win_rate, get_bot_address
            def _compute_summary():
                spent = get_total_spent()
                val = get_estimated_portfolio_value(use_w3)
                opens = get_num_open_positions()
                wr = get_win_rate()
                addr = get_bot_address()
                eth = 0.0
                if use_w3:
                    try:
                        sender = use_w3.eth.account.from_key(os.getenv("PRIVATE_KEY")).address
                        eth = use_w3.eth.get_balance(sender) / 1e18
                    except: pass
                msg = f"📋 SUMMARY (REAL)\nWallet: {addr}\nETH bal: {eth:.4f}\nSpent: {spent:.4f}\nValue: {val:.4f}\nOpens: {opens}\nWinrate: {wr:.1f}%"
                return msg, spent, val
            loop = asyncio.get_running_loop()
            msg, spent, val = await loop.run_in_executor(None, _compute_summary)
            _set_cached('spent', spent)
            _set_cached('portfolio_value', val)
            await query.message.reply_text(msg)
        except Exception as e:
            await query.message.reply_text(f"Error: {e}")

    elif data == "cmd_positions":
        try:
            cached = _get_cached('positions')
            if cached:
                await query.message.reply_text(cached)
                return
            from b20_mainnet_sniper import get_open_positions
            def _compute_positions():
                opens = get_open_positions(_current_w3)
                if not opens:
                    return "📊 No open positions."
                msg = "📊 OPEN POSITIONS (REAL on-chain + DB):\n\n"
                for p in opens:
                    sym = p.get('symbol', '') or ''
                    tshort = p['token'][:10] + "..."
                    label = f"{sym} {tshort}" if sym else tshort
                    msg += f"{label}\n  Acquired: {p.get('acquired',0):.6f} Held: {p.get('held',0):.6f}\n  Spent: {p.get('eth_spent',0):.6f}\n"
                    if p.get('entry_price_eth', 0) > 0:
                        msg += f"  Entry: {p['entry_price_eth']:.10f} ETH/token\n"
                    val = p.get('value_eth', 0)
                    if val > 0:
                        msg += f"  Value: {val:.6f} PnL: {p.get('pnl_eth',0):.6f}\n"
                    else:
                        msg += "  Value/PnL: N/A (fresh token)\n"
                    if p.get('note'):
                        msg += f"  ⚠️ {p['note']}\n"
                    msg += "\n"
                return msg[:4000]
            loop = asyncio.get_running_loop()
            msg = await loop.run_in_executor(None, _compute_positions)
            _set_cached('positions', msg)
            await query.message.reply_text(msg)
        except Exception as e:
            await query.message.reply_text(f"Positions error: {e}")

    elif data == "cmd_gas":
        use_w3 = _current_w3
        if use_w3:
            try:
                from b20_mainnet_sniper import get_gas_info
                def _get_gas():
                    return get_gas_info(use_w3)
                loop = asyncio.get_running_loop()
                g = await loop.run_in_executor(None, _get_gas)
                await query.message.reply_text(f"⛽ REAL Gas: {g.get('gas_price_gwei')} gwei | Base: {g.get('base_fee_gwei')} gwei")
            except Exception as e:
                await query.message.reply_text(f"Gas error: {e}")
        else:
            await query.message.reply_text("No w3 context.")

    elif data == "cmd_activation":
        use_w3 = _current_w3
        if use_w3:
            try:
                from b20_mainnet_sniper import get_activation_status
                def _get_act():
                    return get_activation_status(use_w3)
                loop = asyncio.get_running_loop()
                res = await loop.run_in_executor(None, _get_act)
                await query.message.reply_text(f"🔓 {res} (REAL on-chain)")
            except Exception as e:
                await query.message.reply_text(f"Error: {e}")
        else:
            await query.message.reply_text("No w3.")

    elif data == "cmd_rpc":
        if _cfg:
            rpc = _cfg.get('RPC_URL', 'N/A')[:50]
            backups = len(_cfg.get('BACKUP_RPCS', []))
            await query.message.reply_text(f"🌐 Current RPC: {rpc}...\nBackups: {backups} (real failover list)")
        else:
            await query.message.reply_text("No config.")

    elif data == "cmd_history" or data == "cmd_recent":
        # real recent
        try:
            import sqlite3
            conn = sqlite3.connect("b20_trades.db")
            c = conn.cursor()
            c.execute("SELECT timestamp, token, action, amount, tx_hash, status, COALESCE(token_amount,0) FROM trades ORDER BY id DESC LIMIT 5")
            rows = c.fetchall()
            conn.close()
            if not rows:
                msg = "No history."
            else:
                msg = "📜 Recent (REAL DB):\n"
                for ts, tok, act, amt, txh, st, acq in rows:
                    short_tok = (tok or "")[:8] + "..."
                    msg += f"{ts[:16]} {act} {short_tok} amt={amt} acq={acq:.2f} {st}\n"
            await query.message.reply_text(msg)
        except Exception as e:
            await query.message.reply_text(f"History error: {e}")

    elif data == "cmd_stats":
        try:
            from b20_mainnet_sniper import get_detailed_stats, get_win_rate
            st = get_detailed_stats()
            wr = get_win_rate()
            msg = f"📊 STATS (REAL)\nBuys: {st['successful_buys']}\nSpent: {st['total_spent']:.4f}\nSells: {st['sells']}\nWinrate: {wr:.1f}%"
            await query.message.reply_text(msg)
        except Exception as e:
            await query.message.reply_text(f"Stats error: {e}")

    elif data == "cmd_refresh":
        try:
            from b20_mainnet_sniper import get_open_positions
            def _get_opens():
                return get_open_positions(_current_w3)
            loop = asyncio.get_running_loop()
            opens = await loop.run_in_executor(None, _get_opens)
            await query.message.reply_text(f"🔄 Refreshed. {len(opens)} positions (real on-chain balances).")
        except Exception as e:
            await query.message.reply_text(f"Refresh error: {e}")

    elif data == "cmd_open":
        try:
            from b20_mainnet_sniper import get_open_positions
            def _get_opens():
                return get_open_positions(_current_w3, include_price=False)
            loop = asyncio.get_running_loop()
            opens = await loop.run_in_executor(None, _get_opens)
            if not opens:
                msg = "No open positions."
            else:
                msg = "📂 Open (REAL):\n" + "\n".join([f"- {p.get('symbol','') or p['token'][:8]}... held:{p['held']:.2f}" for p in opens])
            await query.message.reply_text(msg)
        except Exception as e:
            await query.message.reply_text(f"Error: {e}")

    elif data == "cmd_export":
        try:
            from b20_mainnet_sniper import export_trades_csv
            ok = export_trades_csv("tg_export_trades.csv")
            await query.message.reply_text("CSV exported (real DB)." if ok else "Export failed.")
        except Exception as e:
            await query.message.reply_text(f"Error: {e}")

    elif data == "cmd_config":
        if _cfg:
            safe = {k: v for k, v in _cfg.items() if not any(x in k for x in ['KEY', 'TOKEN', 'RPC'])}
            msg = "⚙️ Config (REAL):\n" + "\n".join(f"{k}: {v}" for k, v in list(safe.items())[:10])
            await query.message.reply_text(msg)
        else:
            await query.message.reply_text("No config.")

    elif data == "cmd_liq":
        # For liq, if there are open positions, show for first one, else prompt
        try:
            from b20_mainnet_sniper import get_open_positions, find_best_pool, check_pool_liquidity, get_token_price_in_eth
            def _get_liq():
                opens = get_open_positions(_current_w3)
                if opens:
                    tok = opens[0]['token']
                    pool, fee = find_best_pool(_current_w3, tok)
                    if pool:
                        liq = check_pool_liquidity(_current_w3, pool)
                        price = get_token_price_in_eth(_current_w3, tok)
                        return f"💧 REAL Liq for {tok[:10]}... : {liq/1e18:.4f} ETH (fee {fee})\nPrice ~{price:.10f} ETH"
                    else:
                        return f"No pool for {tok[:10]}. Use /liq <token>"
                else:
                    return "Use /liq <token> for real liquidity (on-chain pool + price)"
            loop = asyncio.get_running_loop()
            msg = await loop.run_in_executor(None, _get_liq)
            await query.message.reply_text(msg)
        except Exception as e:
            await query.message.reply_text(f"Liq error: {e}. Use /liq <token>")

    elif data == "cmd_simulate":
        try:
            from b20_mainnet_sniper import get_open_positions, get_accurate_min_out, find_best_pool
            def _get_sim():
                opens = get_open_positions(_current_w3)
                if opens:
                    tok = opens[0]['token']
                    pool, fee = find_best_pool(_current_w3, tok)
                    if not fee: fee=3000
                    amt = 0.003
                    amount_in = _current_w3.to_wei(amt, 'ether')
                    min_out = get_accurate_min_out(_current_w3, tok, fee, amount_in, 2000)
                    return f"🧪 REAL Simulate 0.003 ETH on {tok[:10]}... -> ~{min_out / 1e18:.4f} tokens (min out)"
                else:
                    return "Use /simulate <token> <eth> for real Quoter output"
            loop = asyncio.get_running_loop()
            msg = await loop.run_in_executor(None, _get_sim)
            await query.message.reply_text(msg)
        except Exception as e:
            await query.message.reply_text(f"Sim error: {e}")

    elif data == "cmd_safety":
        try:
            from b20_mainnet_sniper import get_open_positions, run_token_safety
            def _get_safety():
                opens = get_open_positions(_current_w3)
                if opens:
                    tok = opens[0]['token']
                    res = run_token_safety(_current_w3, tok)
                    return f"🛡️ REAL Safety for {tok[:10]}... : {res}"
                else:
                    return "Use /safety <token> for real on-chain checks (liq, honeypot sim, etc)"
            loop = asyncio.get_running_loop()
            msg = await loop.run_in_executor(None, _get_safety)
            await query.message.reply_text(msg)
        except Exception as e:
            await query.message.reply_text(f"Safety error: {e}")

    elif data == "cmd_perftoken":
        try:
            from b20_mainnet_sniper import get_open_positions, get_token_price_in_eth
            def _get_perf():
                opens = get_open_positions(_current_w3)
                if opens:
                    p = opens[0]
                    price = get_token_price_in_eth(_current_w3, p['token']) if _current_w3 else 0
                    val = p['held'] * price
                    pnl = val - p['eth_spent']
                    return f"📊 REAL Perftoken {p['token'][:10]}...\nHeld: {p['held']:.4f} Spent: {p['eth_spent']:.4f}\nPrice: {price:.10f}\nValue: {val:.6f} PnL: {pnl:.6f}"
                else:
                    return "Use /perftoken <token> for real per-token PnL"
            loop = asyncio.get_running_loop()
            msg = await loop.run_in_executor(None, _get_perf)
            await query.message.reply_text(msg)
        except Exception as e:
            await query.message.reply_text(f"Error: {e}")

    elif data == "cmd_addblack":
        await query.edit_message_text("Use /addblack <token> to add (real blacklist update)")
    elif data == "cmd_remblack":
        await query.edit_message_text("Use /remblack <token> to remove (real)")

    elif data == "cmd_balance":
        await query.message.reply_text("Use /balance <token> for live on-chain balance (real).")

    elif data == "cmd_price":
        await query.message.reply_text("Use /price <token> for live Quoter price (real mainnet).")

    elif data == "cmd_pools":
        await query.message.reply_text("Use /pools <token> for real pool + liq info.")

    elif data == "cmd_tx":
        await query.message.reply_text("Use /tx <hash> for real tx details + basescan link.")

    elif data == "cmd_buy":
        await query.message.reply_text("Use /buy <token> <eth> to manual buy (real tx). Example: /buy 0x... 0.003")

    elif data == "cmd_sell":
        await query.message.reply_text("Use /sell <token> <pct> or the TP buttons after buys. Uses live balance.")

    elif data == "cmd_help" or data == "cmd_commands":
        await query.edit_message_text(
            "All commands via text: /help or /list\n"
            "Menu buttons cover the main ones.\n"
            "All data shown is REAL (on-chain Quoter, balanceOf, DB queries, etc).\n"
            "No dummies."
        )

    elif data == "menu":
        # refresh the main menu
        await query.edit_message_text("B20 Bot Main Menu (LIVE - Real data):", reply_markup=get_main_menu_keyboard())
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

    else:
        # Catch-all to always give some output for any button
        await query.message.reply_text(f"Button '{data}' clicked. No specific handler or output yet.\nTry the text command equivalent or /help.")


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
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("list", help_cmd))
    app.add_handler(CommandHandler("commands", help_cmd))
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
    app.add_handler(CommandHandler("recent", recent_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("activation", activation_cmd))
    app.add_handler(CommandHandler("rpc", rpc_cmd))
    app.add_handler(CommandHandler("perftoken", perftoken_cmd))
    app.add_handler(CommandHandler("refresh", refresh_cmd))
    app.add_handler(CommandHandler("liq", liq_cmd))
    app.add_handler(CommandHandler("simulate", simulate_cmd))
    app.add_handler(CommandHandler("addblack", addblack_cmd))
    app.add_handler(CommandHandler("remblack", remblack_cmd))
    app.add_handler(CommandHandler("lastbuy", lastbuy_cmd))
    app.add_handler(CommandHandler("summary", summary_cmd))
    app.add_handler(CommandHandler("profit", profit_cmd))
    app.add_handler(CommandHandler("checkprofit", profit_cmd))

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
    """Used for outbound alerts. Rich version with real data buttons."""
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Status", "callback_data": "cmd_status"},
                {"text": "📈 PnL", "callback_data": "cmd_pnl"},
                {"text": "📍 Positions", "callback_data": "cmd_positions"},
            ],
            [
                {"text": "⏸️ Pause", "callback_data": "pause"},
                {"text": "▶️ Resume", "callback_data": "resume"},
                {"text": "🛑 Kill", "callback_data": "kill"},
            ],
            [
                {"text": "🔄 Menu", "callback_data": "menu"},
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


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Comprehensive main menu with buttons for (almost) all commands.
    All outputs from these will use real on-chain/DB data.
    """
    keyboard = [
        [
            InlineKeyboardButton("📊 Status", callback_data="cmd_status"),
            InlineKeyboardButton("📈 PnL", callback_data="cmd_pnl"),
            InlineKeyboardButton("📋 Summary", callback_data="cmd_summary"),
        ],
        [
            InlineKeyboardButton("📍 Positions", callback_data="cmd_positions"),
            InlineKeyboardButton("💰 Value", callback_data="cmd_value"),
            InlineKeyboardButton("💸 Spent", callback_data="cmd_spent"),
        ],
        [
            InlineKeyboardButton("⛽ Gas", callback_data="cmd_gas"),
            InlineKeyboardButton("🔓 Activation", callback_data="cmd_activation"),
            InlineKeyboardButton("🌐 RPC", callback_data="cmd_rpc"),
        ],
        [
            InlineKeyboardButton("📜 History", callback_data="cmd_history"),
            InlineKeyboardButton("📊 Stats", callback_data="cmd_stats"),
            InlineKeyboardButton("🕒 Recent", callback_data="cmd_recent"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="cmd_refresh"),
            InlineKeyboardButton("📂 Open", callback_data="cmd_open"),
            InlineKeyboardButton("📤 Export", callback_data="cmd_export"),
        ],
        [
            InlineKeyboardButton("⏸️ Pause", callback_data="pause"),
            InlineKeyboardButton("▶️ Resume", callback_data="resume"),
            InlineKeyboardButton("🛑 Kill", callback_data="kill"),
        ],
        [
            InlineKeyboardButton("💧 Liq", callback_data="cmd_liq"),
            InlineKeyboardButton("🧪 Simulate", callback_data="cmd_simulate"),
            InlineKeyboardButton("🛡️ Safety", callback_data="cmd_safety"),
        ],
        [
            InlineKeyboardButton("📊 Perftoken", callback_data="cmd_perftoken"),
            InlineKeyboardButton("🖤 AddBlack", callback_data="cmd_addblack"),
            InlineKeyboardButton("🖤 RemBlack", callback_data="cmd_remblack"),
        ],
        [
            InlineKeyboardButton("💰 Balance", callback_data="cmd_balance"),
            InlineKeyboardButton("💲 Price", callback_data="cmd_price"),
            InlineKeyboardButton("🏊 Pools", callback_data="cmd_pools"),
        ],
        [
            InlineKeyboardButton("📤 Tx", callback_data="cmd_tx"),
            InlineKeyboardButton("🛒 Buy", callback_data="cmd_buy"),
            InlineKeyboardButton("💸 Sell", callback_data="cmd_sell"),
        ],
        [
            InlineKeyboardButton("⚙️ Config", callback_data="cmd_config"),
            InlineKeyboardButton("❓ Help / List", callback_data="cmd_help"),
            InlineKeyboardButton("🔄 Menu", callback_data="menu"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


if __name__ == "__main__":
    # Standalone test
    print("Starting standalone TG bot test...")
    start_telegram_bot_in_background()
    input("Press Enter to stop...\n")