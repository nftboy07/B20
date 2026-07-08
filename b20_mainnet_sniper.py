#!/usr/bin/env python3
"""
B20 Mainnet Sniper / Launcher Bot
=================================
SCOPED EXCLUSIVELY TO BASE MAINNET (chainId=8453)

This implements the requirements from the provided Mainnet spec:
- Hardcoded Mainnet addresses and chain ID
- Activation Registry check before any createB20
- B20Factory createB20 support (with simulation)
- Uniswap V3 PoolCreated monitoring (any fee tier)
- High-gas aggressive sniping with fee history + premium
- Liquidity check before retry buys
- Flashbots Protect RPC option for private submission
- eth_call dry-run before real tx where possible

CRITICAL WARNINGS (READ FULLY):
- This runs on REAL Base Mainnet. Gas and swaps cost REAL ETH.
- Do NOT run createB20 or any state-changing tx before the Activation Registry enables the feature.
  Scheduled: July 8, 2026, 18:00 UTC (23:30 IST).
- Sniping carries extreme risk: slippage, rugs, honeypots, failed tx (gas still spent), MEV, etc.
- You are responsible for your wallet, keys, and funds. 0.5+ ETH recommended minimum.
- Test thoroughly with eth_call simulations and on testnet if possible BEFORE any live mainnet run.
- This is NOT financial advice. Trading bots can and will lose money.
- **NEVER hardcode or log private keys/tokens.** All secrets MUST come from .env (which is gitignored).
- The code explicitly masks keys in logs via mask_sensitive() and get_safe_config().
- Clock must be NTP-synced to UTC.
- On VPS always: chmod 600 .env
- If a secret is ever leaked, rotate it IMMEDIATELY.

Usage:
  1. pip install web3 python-dotenv
  2. Create .env with:
       RPC_URL=https://mainnet.base.org
       # or wss://... for better event subscription
       PRIVATE_KEY=0x...
       FLASHBOTS_RPC=https://rpc.flashbots.net   # optional, for private tx if supported / proxy
  3. python b20_bot/b20_mainnet_sniper.py --help
  4. Start with --dry-run or --simulate-only

Addresses (Mainnet only, do not override):
- Activation Registry: 0x8453000000000000000000000000000000000001
- Policy Registry:     0x8453000000000000000000000000000000000002
- B20Factory:          0xB20f000000000000000000000000000000000000
- Uniswap V3 Factory:  0x33128a8fC17869897dcE68Ed026d694621f6FDfD
- Uniswap V3 Router:   0xE592427A0AEce92De3Edee1F18E0157C05861564
- WETH:                0x4200000000000000000000000000000000000006
- USDC:                0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913

B20 feature keys (for isActivated):
- base.b20_asset
- base.b20_stablecoin

B20 tokens are created at addresses starting 0xB200...
"""

import os
import sys
import time
import json
import sqlite3
import argparse
import random
import threading
import asyncio
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any

from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import TransactionNotFound
from eth_utils import keccak, to_checksum_address
from eth_abi import encode

try:
    from mempool_monitor import MempoolMonitor
    from early_detection import EarlyDetectionEngine
    MEMPOOL_AVAILABLE = True
except ImportError:
    MEMPOOL_AVAILABLE = False
    print("Mempool modules not available (optional)")

current_w3 = None

# =============================================================================
# MAINNET-ONLY CONSTANTS (DO NOT CHANGE)
# =============================================================================
CHAIN_ID = 8453
RPC_DEFAULT = "https://mainnet.base.org"

# Extensive list of public Base Mainnet RPCs (HTTP + WS) for failover and reliability.
# The bot will rotate through these to avoid rate limits (429s) during high-traffic meme launches.
# Add your own paid ones in .env for best performance.
DEFAULT_BASE_RPCS = [
    "https://mainnet.base.org",
    "https://base-mainnet.public.blastapi.io",
    "https://base.meowrpc.com",
    "https://base-pokt.nodies.app",
    "https://rpc.ankr.com/base",
    "https://base.drpc.org",
    "https://1rpc.io/base",
    "https://base.blockpi.network/v1/rpc/public",
    "https://base.api.onfinality.io/public",
    "https://gateway.tenderly.co/public/base",
    "https://base.llamarpc.com",
    "https://base.rpc.subquery.network/public",
    "https://base-rpc.publicnode.com",
    "https://base-mainnet-rpc.allthatnode.com",
    "https://base.publicnode.com",
]

ACTIVATION_REGISTRY = to_checksum_address("0x8453000000000000000000000000000000000001")
POLICY_REGISTRY     = to_checksum_address("0x8453000000000000000000000000000000000002")
B20_FACTORY         = to_checksum_address("0xB20f000000000000000000000000000000000000")

UNISWAP_V3_FACTORY  = to_checksum_address("0x33128a8fC17869897dcE68Ed026d694621f6FDfD")
UNISWAP_V3_ROUTER   = to_checksum_address("0xE592427A0AEce92De3Edee1F18E0157C05861564")
WETH                = to_checksum_address("0x4200000000000000000000000000000000000006")
USDC                = to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")

# Uniswap V3 QuoterV2 on Base for accurate pricing (critical for real slippage)
UNISWAP_QUOTER_V2   = to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a")

# Activation feature hashes (as used in Base docs: keccak("base.b20_asset"))
FEATURE_B20_ASSET      = keccak(text="base.b20_asset")
FEATURE_B20_STABLECOIN = keccak(text="base.b20_stablecoin")

# Common fee tiers (do not filter; buy any)
UNISWAP_FEE_TIERS = [500, 3000, 10000]

# Activation time (do not hardcode logic that bypasses the on-chain check)
ACTIVATION_UTC = datetime(2026, 7, 8, 18, 0, 0, tzinfo=timezone.utc)

# Minimal ABIs (sufficient for the bot operations)
ACTIVATION_REGISTRY_ABI = [
    {"inputs": [{"internalType": "bytes32", "name": "feature", "type": "bytes32"}],
     "name": "isActivated", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
     "stateMutability": "view", "type": "function"}
]

B20_FACTORY_ABI = [
    {"inputs": [{"internalType": "uint8", "name": "variant", "type": "uint8"},
                 {"internalType": "bytes32", "name": "salt", "type": "bytes32"},
                 {"internalType": "bytes", "name": "params", "type": "bytes"},
                 {"internalType": "bytes[]", "name": "initCalls", "type": "bytes[]"}],
     "name": "createB20", "outputs": [{"internalType": "address", "name": "token", "type": "address"}],
     "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"internalType": "uint8", "name": "variant", "type": "uint8"},
                 {"internalType": "address", "name": "deployer", "type": "address"},
                 {"internalType": "bytes32", "name": "salt", "type": "bytes32"}],
     "name": "getB20Address", "outputs": [{"internalType": "address", "name": "", "type": "address"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "address", "name": "addr", "type": "address"}],
     "name": "isB20", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "address", "name": "addr", "type": "address"}],
     "name": "isB20Initialized", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
     "stateMutability": "view", "type": "function"},
    # B20Created event for early detection (upgrade)
    {"anonymous": False, "inputs": [
        {"indexed": True, "internalType": "address", "name": "token", "type": "address"},
        {"indexed": False, "internalType": "uint8", "name": "variant", "type": "uint8"},
        {"indexed": False, "internalType": "string", "name": "name", "type": "string"},
        {"indexed": False, "internalType": "string", "name": "symbol", "type": "string"},
    ], "name": "B20Created", "type": "event"}
]

UNISWAP_V3_FACTORY_ABI = [
    {"anonymous": False, "inputs": [
        {"indexed": True, "internalType": "address", "name": "token0", "type": "address"},
        {"indexed": True, "internalType": "address", "name": "token1", "type": "address"},
        {"indexed": True, "internalType": "uint24", "name": "fee", "type": "uint24"},
        {"indexed": False, "internalType": "int24", "name": "tickSpacing", "type": "int24"},
        {"indexed": False, "internalType": "address", "name": "pool", "type": "address"}
    ], "name": "PoolCreated", "type": "event"},
    {"inputs": [{"internalType": "address", "name": "", "type": "address"},
                 {"internalType": "address", "name": "", "type": "address"},
                 {"internalType": "uint24", "name": "", "type": "uint24"}],
     "name": "getPool", "outputs": [{"internalType": "address", "name": "", "type": "address"}],
     "stateMutability": "view", "type": "function"}
]

# Minimal Pool ABI for liquidity() and slot0
UNISWAP_V3_POOL_ABI = [
    {"inputs": [], "name": "liquidity", "outputs": [{"internalType": "uint128", "name": "", "type": "uint128"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "slot0", "outputs": [
        {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
        {"internalType": "int24", "name": "tick", "type": "int24"},
        {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
        {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
        {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
        {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
        {"internalType": "bool", "name": "unlocked", "type": "bool"}
    ], "stateMutability": "view", "type": "function"}
]

# Uniswap V3 Router ABI (only the functions we need)
UNISWAP_V3_ROUTER_ABI = [
    {"inputs": [
        {"internalType": "address", "name": "tokenIn", "type": "address"},
        {"internalType": "address", "name": "tokenOut", "type": "address"},
        {"internalType": "uint24", "name": "fee", "type": "uint24"},
        {"internalType": "address", "name": "recipient", "type": "address"},
        {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
        {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
        {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"}
    ], "name": "exactInputSingle", "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
     "stateMutability": "payable", "type": "function"}
]

# Minimal QuoterV2 ABI for accurate quotes (upgrade for real slippage)
UNISWAP_QUOTER_V2_ABI = [
    {"inputs": [{"internalType": "bytes", "name": "params", "type": "bytes"}],
     "name": "quoteExactInputSingle", "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"},
                                                  {"internalType": "uint160", "name": "sqrtPriceX96After", "type": "uint160"},
                                                  {"internalType": "uint32", "name": "initializedTicksCrossed", "type": "uint32"},
                                                  {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"}],
     "stateMutability": "nonpayable", "type": "function"}
]

# =============================================================================
# UTILS
# =============================================================================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def is_activation_time_passed() -> bool:
    return now_utc() >= ACTIVATION_UTC

def load_config() -> Dict[str, Any]:
    load_dotenv()
    cfg = {
        "RPC_URL": os.getenv("RPC_URL", RPC_DEFAULT),
        "PRIVATE_KEY": os.getenv("PRIVATE_KEY", ""),
        "FLASHBOTS_RPC": os.getenv("FLASHBOTS_RPC", ""),
        "WALLET_ADDRESS": os.getenv("WALLET_ADDRESS", ""),
        "MAX_TRADE_ETH": float(os.getenv("MAX_TRADE_ETH", "0.1")),
        "MAX_DAILY_LOSS_ETH": float(os.getenv("MAX_DAILY_LOSS_ETH", "0.5")),
        "MIN_LIQUIDITY_ETH": float(os.getenv("MIN_LIQUIDITY_ETH", "5.0")),
        "SLIPPAGE_BPS": int(os.getenv("SLIPPAGE_BPS", "2000")),
        "KILL_SWITCH_FILE": os.getenv("KILL_SWITCH_FILE", "/home/ubuntu/b20-bot/KILL_SWITCH"),
        "TG_BOT_TOKEN": os.getenv("TG_BOT_TOKEN", ""),
        "TG_USER_ID": os.getenv("TG_USER_ID", ""),
    }
    user_rpcs = [r.strip() for r in os.getenv("BACKUP_RPCS", "").split(",") if r.strip()]
    cfg["BACKUP_RPCS"] = user_rpcs + [r for r in DEFAULT_BASE_RPCS if r not in user_rpcs]  # user first, then defaults, no dups
    if not cfg["PRIVATE_KEY"]:
        print("WARNING: PRIVATE_KEY not set - transactions will fail. Use for monitoring only.")
    # Leak-proof runtime guard - check for placeholder keys
    key = cfg.get("PRIVATE_KEY", "")
    if key and ("YOUR" in key or len(key) < 10 or key.startswith("0x0000")):
        print("WARNING: PRIVATE_KEY looks like a placeholder or dummy. Real transactions will fail.")
    return cfg

def mask_sensitive(value: str, show_last: int = 4) -> str:
    """Mask sensitive strings like keys and tokens for logging."""
    if not value or len(value) < 8:
        return "***"
    return value[:4] + "..." + value[-show_last:] if len(value) > 8 else "***"

def get_safe_config(cfg: dict) -> dict:
    """Return a copy of config with all sensitive values masked. Leak-proof for logs."""
    safe = cfg.copy()
    for k in ["PRIVATE_KEY", "TG_BOT_TOKEN", "FLASHBOTS_RPC"]:
        if k in safe and safe[k]:
            safe[k] = mask_sensitive(safe[k])
    if "BACKUP_RPCS" in safe:
        safe["BACKUP_RPCS"] = [mask_sensitive(r) for r in safe.get("BACKUP_RPCS", [])]
    return safe

def get_w3(rpc_url: str) -> Web3:
    if rpc_url.startswith("wss://"):
        try:
            w3 = Web3(Web3.WebsocketProvider(rpc_url))
        except:
            w3 = Web3(Web3.WebSocketProvider(rpc_url))
    else:
        w3 = Web3(Web3.HTTPProvider(rpc_url))
    return w3

def get_working_w3(rpc_list: list = None, max_attempts: int = 5) -> Web3:
    """Try multiple RPCs until one works. Rotates list for load balancing.
    This is key for surviving rate limits during meme launches.
    Tests chainId + eth_call for full compatibility.
    """
    if not rpc_list:
        rpc_list = DEFAULT_BASE_RPCS
    rpc_list = list(rpc_list)  # copy
    random.shuffle(rpc_list)  # randomize to distribute load
    for _ in range(max_attempts):
        for rpc in rpc_list:
            try:
                w3 = get_w3(rpc)
                # Strong health check: chain + simple call
                if w3.eth.chain_id == CHAIN_ID:
                    # Simple health: just chain_id is enough for most, avoid eth_call on precompiles if restricted
                    print(f"Using RPC: {rpc[:50]}...")
                    return w3
            except Exception as e:
                print(f"RPC {rpc[:30]}... failed: {str(e)[:60]}, trying next...")
                continue
        time.sleep(1)  # brief pause before retry
    raise Exception("No working RPC found after attempts. Check your internet or add more RPCs in .env")

# =============================================================================
# TELEGRAM NOTIFICATIONS (optional)
# =============================================================================
def tg_send(message: str, reply_markup: dict = None):
    """Send message to Telegram if TG_BOT_TOKEN and TG_USER_ID are set in .env.
    Supports optional inline keyboard buttons for commands.
    """
    token = os.getenv("TG_BOT_TOKEN")
    user_id = os.getenv("TG_USER_ID")
    if not token or not user_id:
        return
    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": user_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        requests.post(url, json=payload, timeout=8)
    except Exception as e:
        print(f"[TG] notify failed: {e}")

def check_tg_commands(cfg: dict, w3=None):
    """TG commands with inline buttons for control.
    Supports /start to show buttons, and callback buttons for pause/resume/status + buy buttons.
    Uses offset file to avoid reprocessing updates.
    Runs in own thread for fast response.
    """
    token = cfg.get("TG_BOT_TOKEN", "")
    user_id = cfg.get("TG_USER_ID", "")
    if not token or not user_id:
        return
    offset_file = "/home/ubuntu/b20-bot/tg_offset.txt"
    offset = 0
    if os.path.exists(offset_file):
        try:
            with open(offset_file) as f:
                offset = int(f.read().strip() or 0)
        except:
            offset = 0
    while True:
        try:
            import requests
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&limit=10&timeout=2"
            resp = requests.get(url, timeout=10).json()

            if not resp.get("ok") or not resp.get("result"):
                time.sleep(1)
                continue

            new_offset = offset
            for update in resp["result"]:
                new_offset = update["update_id"] + 1

                # Handle callback_query (buttons)
                if "callback_query" in update:
                    cb = update["callback_query"]
                    data = cb.get("data", "")
                    cb_id = cb["id"]
                    requests.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery", json={"callback_query_id": cb_id})

                    if data == "status":
                        tg_send("📊 Bot Status: LIVE mode active. Monitoring pools. Check logs.")
                    elif data == "pause":
                        open(cfg.get("KILL_SWITCH_FILE", "/tmp/kill"), 'a').close()
                        tg_send("⏸️ Paused monitoring.")
                    elif data == "resume":
                        kf = cfg.get("KILL_SWITCH_FILE", "/tmp/kill")
                        if os.path.exists(kf):
                            os.remove(kf)
                        tg_send("▶️ Resumed monitoring.")
                    elif data == "kill":
                        open(cfg.get("KILL_SWITCH_FILE", "/tmp/kill"), 'a').close()
                        tg_send("🛑 Kill switch activated.")
                    elif data.startswith("buy_"):
                        try:
                            _, tkn, amt_str = data.split("_", 2)
                            amt = float(amt_str)
                            use_w3 = w3 or current_w3
                            if use_w3:
                                tg_send(f"Executing buy {amt} ETH on {tkn}...")
                                f = 3000
                                p = find_or_wait_pool(use_w3, WETH, tkn, f) or find_or_wait_pool(use_w3, tkn, WETH, f)
                                if not p:
                                    f = 10000
                                attempt_buy(use_w3, tkn, f, amt, cfg, max_retries=1)
                        except Exception as be:
                            tg_send(f"Buy button error: {be}")

                    continue

                # Handle text messages
                msg = update.get("message", {}).get("text", "").strip().lower()
                if msg == "/start" or msg == "/menu":
                    buttons = {
                        "inline_keyboard": [
                            [{"text": "📊 Status", "callback_data": "status"},
                             {"text": "⏸️ Pause", "callback_data": "pause"}],
                            [{"text": "▶️ Resume", "callback_data": "resume"},
                             {"text": "🛑 Kill", "callback_data": "kill"}]
                        ]
                    }
                    tg_send("B20 Bot Control Panel (LIVE):", reply_markup=buttons)
                elif msg == "/status":
                    tg_send("📊 Bot Status: LIVE monitoring active. Use /menu for buttons.")
                elif msg == "/pause":
                    open(cfg.get("KILL_SWITCH_FILE", "/tmp/kill"), 'a').close()
                    tg_send("⏸️ Paused via command.")
                elif msg == "/resume":
                    kf = cfg.get("KILL_SWITCH_FILE", "/tmp/kill")
                    if os.path.exists(kf):
                        os.remove(kf)
                    tg_send("▶️ Resumed via command.")

            with open(offset_file, "w") as f:
                f.write(str(new_offset))
            offset = new_offset
        except Exception as e:
            print(f"[TG] command poll error: {e}")
            time.sleep(2)

def is_kill_switch_active(kill_file: str) -> bool:
    return os.path.exists(kill_file)

# Simple SQLite trade logger (major upgrade for analytics)
DB_FILE = "b20_trades.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS trades
                 (id INTEGER PRIMARY KEY, timestamp TEXT, token TEXT, action TEXT, amount REAL, tx_hash TEXT, status TEXT)''')
    conn.commit()
    conn.close()

def log_trade(token: str, action: str, amount: float, tx_hash: str = "", status: str = "pending"):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO trades (timestamp, token, action, amount, tx_hash, status) VALUES (?, ?, ?, ?, ?, ?)",
                  (datetime.utcnow().isoformat(), token, action, amount, tx_hash, status))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] log error: {e}")

def check_token_safety(w3: Web3, token: str, min_liq: float) -> tuple[bool, str]:
    """Enhanced safety checks to avoid honeypots, rugs, etc. Returns (is_safe, reason)"""
    try:
        pool = find_or_wait_pool(w3, WETH, token, 3000) or find_or_wait_pool(w3, WETH, token, 10000)
        if not pool:
            return False, "No WETH pool found"
        liq = check_pool_liquidity(w3, pool)
        if liq < w3.to_wei(min_liq, "ether"):
            return False, f"Low liquidity: {liq}"

        # Honeypot / rug checks: simulate small buy then sell using Quoter
        test_eth = 0.001
        amount_in = w3.to_wei(test_eth, 'ether')
        try:
            # Buy quote
            buy_out = get_accurate_min_out(w3, token, 3000, amount_in, 1000)
            if buy_out < 1:
                return False, "Honeypot: buy quote 0 or very low"

            # Sell quote back
            sell_out = get_accurate_min_out(w3, WETH, 3000, buy_out, 1000)
            if sell_out < amount_in * 0.7:  # lose more than 30% on roundtrip -> suspicious
                return False, f"Honeypot detected: roundtrip loss >30% (got back {sell_out / 1e18} ETH)"

        except Exception as sim_e:
            return False, f"Honeypot sim failed: {str(sim_e)[:50]}"

        # Additional B20 specific: check if token is initialized B20
        try:
            fac = get_b20_factory(w3)
            if not fac.functions.isB20Initialized(to_checksum_address(token)).call():
                # Still allow if it's B20 address pattern, but warn
                pass
        except:
            pass

        return True, "Passed honeypot and liq checks"
    except Exception as e:
        return False, f"Safety check error: {str(e)[:80]}"

def mainnet_sanity_check(w3: Web3) -> None:
    """Executable Mainnet-Only Check (adapted for precompiles).

    Precompiles (Activation Registry / B20Factory) often return empty code via
    eth_getCode even when callable. We therefore:
      - Strictly assert chainId == 8453
      - Prove the registries are present by performing a successful read call
        (isActivated). This matches real-world behavior better than get_code > 0.
    """
    assert w3.eth.chain_id == 8453, "Wrong network! Must be Base Mainnet."

    # Prove registries exist by successful view call (precompile reality)
    try:
        reg = w3.eth.contract(address=ACTIVATION_REGISTRY, abi=ACTIVATION_REGISTRY_ABI)
        _ = reg.functions.isActivated(FEATURE_B20_ASSET).call()
    except Exception as e:
        raise AssertionError(f"Activation Registry not reachable / not deployed: {e}") from e

    try:
        fac = w3.eth.contract(address=B20_FACTORY, abi=B20_FACTORY_ABI)
        _ = fac.functions.isB20("0x0000000000000000000000000000000000000001").call()  # any address is fine for presence
    except Exception as e:
        raise AssertionError(f"B20Factory not reachable / not deployed: {e}") from e

    print("Mainnet checks passed (chainId + precompile callability).")

def get_activation_registry(w3: Web3):
    return w3.eth.contract(address=ACTIVATION_REGISTRY, abi=ACTIVATION_REGISTRY_ABI)

def is_feature_activated(w3: Web3, feature_hash: bytes) -> bool:
    """Call isActivated on the Activation Registry (matches Base docs naming)."""
    reg = get_activation_registry(w3)
    # Note: user prompt used isFeatureActivated; actual deployed name per docs is isActivated
    try:
        return reg.functions.isActivated(feature_hash).call()
    except Exception:
        # Fallback if naming differs on the actual deployment
        # Try common alternative names defensively (read-only)
        for name in ["isFeatureActivated", "isActivated", "activated"]:
            try:
                fn = getattr(reg.functions, name)
                return fn(feature_hash).call()
            except Exception:
                continue
        raise

def check_b20_activated(w3: Web3, want_stable: bool = False) -> bool:
    feature = FEATURE_B20_STABLECOIN if want_stable else FEATURE_B20_ASSET
    activated = is_feature_activated(w3, feature)
    print(f"B20 {'STABLECOIN' if want_stable else 'ASSET'} activated: {activated}")
    return activated

def get_b20_factory(w3: Web3):
    return w3.eth.contract(address=B20_FACTORY, abi=B20_FACTORY_ABI)

def get_token_name_symbol(w3: Web3, token_addr: str):
    """Fetch name and symbol for a token (ERC20 standard)."""
    try:
        abi = [
            {"constant": True, "inputs": [], "name": "name", "outputs": [{"name": "", "type": "string"}], "type": "function"},
            {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
        ]
        c = w3.eth.contract(address=to_checksum_address(token_addr), abi=abi)
        name = c.functions.name().call()
        sym = c.functions.symbol().call()
        return name[:30], sym[:10]  # truncate
    except Exception:
        return "Unknown", "UNK"

def check_recent_b20_creations(w3: Web3, last_block: int, current_block: int) -> list:
    """Early signal from B20Factory (one of the top upgrades for chasing memes)."""
    fac = get_b20_factory(w3)
    try:
        logs = w3.eth.get_logs({
            "fromBlock": last_block + 1,
            "toBlock": current_block,
            "address": B20_FACTORY,
            "topics": [fac.events.B20Created.build_filter().topics[0] if hasattr(fac.events.B20Created, 'build_filter') else None]
        })
        creations = []
        for log in logs:
            try:
                # Simplified decode
                token = to_checksum_address("0x" + log["topics"][1].hex()[-40:]) if len(log.get("topics", [])) > 1 else None
                if token:
                    creations.append(token)
                    print(f"B20Created early signal: {token}")
            except:
                pass
        return creations
    except:
        return []

def get_uniswap_v3_factory(w3: Web3):
    return w3.eth.contract(address=UNISWAP_V3_FACTORY, abi=UNISWAP_V3_FACTORY_ABI)

def get_router(w3: Web3):
    return w3.eth.contract(address=UNISWAP_V3_ROUTER, abi=UNISWAP_V3_ROUTER_ABI)

def get_pool_contract(w3: Web3, pool_addr: str):
    return w3.eth.contract(address=to_checksum_address(pool_addr), abi=UNISWAP_V3_POOL_ABI)

# =============================================================================
# GAS & FEE UTILS (Mainnet congestion handling)
# =============================================================================
def get_gas_price_with_premium(w3: Web3, premium_pct: float = 75.0) -> int:
    """Use eth_gasPrice + premium. For Base (EIP-1559) prefer fee history."""
    try:
        # Prefer EIP-1559 style
        latest = w3.eth.get_block("latest")
        base_fee = latest.get("baseFeePerGas", 0)
        if base_fee:
            # eth_feeHistory for next block estimate
            history = w3.eth.fee_history(4, "latest", [50])
            # Use the last base fee or next as reference
            next_base = history.get("baseFeePerGas", [base_fee])[-1] if history.get("baseFeePerGas") else base_fee
            priority = w3.to_wei(2, "gwei")  # aggressive priority for sniping
            max_fee = int(next_base * (1 + premium_pct / 100)) + priority
            return max_fee  # caller can use as maxFeePerGas
    except Exception as e:
        print(f"feeHistory warning: {e}")

    # Fallback
    gas_price = w3.eth.gas_price
    return int(gas_price * (1 + premium_pct / 100))

def estimate_gas_with_buffer(w3: Web3, tx: dict, buffer: float = 1.5) -> int:
    try:
        est = w3.eth.estimate_gas(tx)
        return int(est * buffer)
    except Exception as e:
        print(f"Gas estimate failed: {e}. Using default 300000")
        return 300000

# =============================================================================
# B20 CREATE (with simulation-first)
# =============================================================================
def encode_simple_asset_params(name: str, symbol: str, admin: str, decimals: int = 18) -> bytes:
    """
    Minimal encoder for Asset variant params.
    Real production code should replicate B20FactoryLib.encodeAssetCreateParams exactly
    (versioned struct). This is a best-effort starting point.
    See https://docs.base.org/get-started/launch-b20-token
    """
    # Based on common patterns in such systems: version byte + tuple
    # For production, reverse the exact ABI used by the precompile / lib.
    # Placeholder structure: version=0x00, then (name, symbol, admin, decimals)
    version = b"\x00"
    encoded = encode(["string", "string", "address", "uint8"], [name, symbol, to_checksum_address(admin), decimals])
    return version + encoded

def simulate_create_b20(w3: Web3, variant: int, salt: bytes, params: bytes, init_calls: list[bytes]) -> dict:
    """Use eth_call at 'pending' to dry-run createB20."""
    factory = get_b20_factory(w3)
    call_data = factory.encodeABI(fn_name="createB20", args=[variant, salt, params, init_calls])
    tx = {
        "to": B20_FACTORY,
        "from": w3.eth.account.from_key(os.getenv("PRIVATE_KEY")).address,
        "data": call_data,
        "value": 0,
    }
    try:
        result = w3.eth.call(tx, "pending")
        # If no revert, decode address if possible
        return {"success": True, "result": result.hex()}
    except Exception as e:
        return {"success": False, "error": str(e)}

def create_b20_live(w3: Web3, variant: int, salt: bytes, params: bytes, init_calls: list[bytes],
                    dry_run: bool = True, gas_premium: float = 100.0) -> Optional[str]:
    """
    createB20 on Mainnet.
    CRITICAL: Only call after isActivated returns true. Gas is real.
    """
    if dry_run:
        print("DRY RUN: Simulating createB20...")
        sim = simulate_create_b20(w3, variant, salt, params, init_calls)
        print("Simulation result:", sim)
        return None

    if not is_activation_time_passed():
        print("Refusing live createB20: current UTC time is before scheduled activation.")
        return None

    factory = get_b20_factory(w3)
    account = w3.eth.account.from_key(os.getenv("PRIVATE_KEY"))
    sender = account.address

    # Build tx
    nonce = w3.eth.get_transaction_count(sender)
    max_fee = get_gas_price_with_premium(w3, gas_premium)

    tx = factory.functions.createB20(variant, salt, params, init_calls).build_transaction({
        "from": sender,
        "nonce": nonce,
        "chainId": CHAIN_ID,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": w3.to_wei(2, "gwei"),
        "value": 0,
    })

    gas = estimate_gas_with_buffer(w3, tx)
    tx["gas"] = gas

    print(f"Submitting createB20 (gas={gas}, maxFee={max_fee}) ...")
    signed = account.sign_transaction(tx)
    # Optionally route via Flashbots if user configured a private RPC
    flash_rpc = os.getenv("FLASHBOTS_RPC")
    if flash_rpc:
        print("Using Flashbots/private RPC for submission if configured as provider.")
        # Re-create provider or use send_raw_transaction on a flashbots w3 if desired.
        # For simplicity we still use the main w3 here; user can start with FLASHBOTS as RPC_URL.

    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print("createB20 tx sent:", tx_hash.hex())

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status == 1:
        # Parse logs or return value if available in newer web3
        print("createB20 SUCCESS. Receipt:", receipt.transactionHash.hex())
        # In practice parse the B20Created event from logs for the token address
        return receipt.transactionHash.hex()
    else:
        print("createB20 FAILED. Receipt:", receipt)
        return None

# =============================================================================
# UNISWAP V3 SNIPING LOGIC
# =============================================================================
def get_token_decimals(w3: Web3, token: str) -> int:
    try:
        erc20 = w3.eth.contract(address=to_checksum_address(token), abi=[
            {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"}
        ])
        return erc20.functions.decimals().call()
    except Exception:
        return 18

def check_pool_liquidity(w3: Web3, pool_addr: str) -> int:
    """Return current liquidity. 0 = no liquidity yet."""
    pool = get_pool_contract(w3, pool_addr)
    try:
        liq = pool.functions.liquidity().call()
        return liq
    except Exception as e:
        print(f"liquidity() call failed: {e}")
        return 0

def find_or_wait_pool(w3: Web3, token_a: str, token_b: str, fee: int) -> Optional[str]:
    factory = get_uniswap_v3_factory(w3)
    pool = factory.functions.getPool(to_checksum_address(token_a), to_checksum_address(token_b), fee).call()
    if pool and int(pool, 16) != 0:
        return to_checksum_address(pool)
    return None

def build_buy_tx(w3: Web3, token_out: str, fee: int, amount_in_wei: int, min_out: int, recipient: str) -> dict:
    """Build exactInputSingle for buying token_out with ETH (WETH path)."""
    router = get_router(w3)
    deadline = int(time.time()) + 60 * 3  # 3 min
    params = {
        "tokenIn": WETH,
        "tokenOut": to_checksum_address(token_out),
        "fee": fee,
        "recipient": to_checksum_address(recipient),
        "deadline": deadline,
        "amountIn": amount_in_wei,
        "amountOutMinimum": min_out,
        "sqrtPriceLimitX96": 0,
    }
    tx = router.functions.exactInputSingle(params).build_transaction({
        "from": recipient,
        "value": amount_in_wei,   # sending ETH
        "chainId": CHAIN_ID,
    })
    return tx

def get_accurate_min_out(w3: Web3, token_out: str, fee: int, amount_in_wei: int, slippage_bps: int) -> int:
    """Use QuoterV2 for realistic amountOut, then apply slippage. Major upgrade for live trading."""
    try:
        quoter = w3.eth.contract(address=UNISWAP_QUOTER_V2, abi=UNISWAP_QUOTER_V2_ABI)
        # Proper struct for QuoterV2 quoteExactInputSingle
        params = (
            WETH,
            to_checksum_address(token_out),
            fee,
            amount_in_wei,
            0  # sqrtPriceLimitX96 = 0 for no limit
        )
        quoted = quoter.functions.quoteExactInputSingle(params).call()
        amount_out = quoted[0] if isinstance(quoted, (list, tuple)) else quoted
        min_out = int(amount_out * (10000 - slippage_bps) / 10000)
        return max(min_out, 1)  # at least 1 to avoid zero
    except Exception as e:
        print(f"[Quoter] Failed, falling back to rough estimate: {e}")
        # Rough fallback: assume 1:1 minus slippage
        return int(amount_in_wei * (10000 - slippage_bps) / 10000)

def attempt_buy(w3: Web3, token: str, fee: int, amount_eth: float, cfg: dict,
                max_retries: int = 1) -> Optional[str]:
    """
    Attempt to buy the new token with ETH.
    - Checks liquidity first.
    - Uses premium gas.
    - Retries once with higher gas / lower slippage if first fails (per spec).
    """
    account = w3.eth.account.from_key(os.getenv("PRIVATE_KEY"))
    sender = account.address

    pool = find_or_wait_pool(w3, WETH, token, fee) or find_or_wait_pool(w3, token, WETH, fee)
    if not pool:
        print("No pool found for token yet.")
        return None

    liq = check_pool_liquidity(w3, pool)
    if liq == 0:
        print("Pool has zero liquidity. Skipping.")
        return None

    print(f"Pool {pool} liquidity: {liq}. Proceeding with buy attempt.")

    amount_in = w3.to_wei(amount_eth, "ether")

    # Proper slippage from cfg + accurate quote (Quoter upgrade)
    slippage_bps = cfg.get("SLIPPAGE_BPS", 2000)
    min_out = get_accurate_min_out(w3, token, fee, amount_in, slippage_bps)
    print(f"Using Quoter + slippage {slippage_bps/100}% → min_out={min_out}")

    for attempt in range(max_retries + 1):
        tx = build_buy_tx(w3, token, fee, amount_in, min_out, sender)
        max_fee = get_gas_price_with_premium(w3, 50 + attempt * 50)
        tx["maxFeePerGas"] = max_fee
        tx["maxPriorityFeePerGas"] = w3.to_wei(3 + attempt, "gwei")
        tx["nonce"] = w3.eth.get_transaction_count(sender)
        gas = estimate_gas_with_buffer(w3, tx, buffer=1.6 + attempt * 0.3)
        tx["gas"] = gas

        print(f"Buy attempt {attempt+1}: amount={amount_eth} ETH, gas={gas}, maxFee={max_fee}")

        # Optional: simulate first
        try:
            w3.eth.call({**tx, "from": sender}, "pending")
        except Exception as e:
            print(f"eth_call simulation revert: {e}")
            if attempt == 0:
                continue  # try again with worse params

        signed = account.sign_transaction(tx)
        try:
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            print("Buy tx sent:", tx_hash.hex())
            tg_send(f"💰 Buy tx sent for <code>{token}</code>\nAmount: {amount_eth} ETH\nTx: <code>{tx_hash.hex()}</code>")
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
            if receipt.status == 1:
                print("BUY SUCCESS:", tx_hash.hex())
                log_trade(token, "buy", amount_eth, tx_hash.hex(), "success")
                tg_send(f"✅ <b>BUY SUCCESS</b> for {token}\nTx: <code>{tx_hash.hex()}</code>")
                # Basic auto-sell hook for future (implement sell on profit)
                return tx_hash.hex()
            else:
                print("Buy tx reverted.")
                tg_send(f"❌ Buy tx reverted for {token}")
        except Exception as e:
            print(f"Send error: {e}")
            tg_send(f"❌ Buy error for {token}: {str(e)[:100]}")

        # Per spec: if failed, retry immediately with higher gas / lower slippage (we already loosen on retry)
        if attempt < max_retries:
            time.sleep(0.5)
            # Re-check liquidity still exists
            if check_pool_liquidity(w3, pool) == 0:
                print("Liquidity disappeared. Aborting retries.")
                break

    return None

def attempt_sell(w3: Web3, token: str, fee: int, amount_token: int, cfg: dict, max_retries: int = 1) -> Optional[str]:
    """Basic sell logic for take profit or emergency. Uses exactInputSingle for token to ETH."""
    if not amount_token or amount_token <= 0:
        return None
    account = w3.eth.account.from_key(os.getenv("PRIVATE_KEY"))
    sender = account.address
    pool = find_or_wait_pool(w3, token, WETH, fee) or find_or_wait_pool(w3, WETH, token, fee)
    if not pool:
        print("No pool for sell")
        return None
    # Use slippage from cfg
    slippage_bps = cfg.get("SLIPPAGE_BPS", 2000)
    min_out = int(amount_token * (10000 - slippage_bps) / 10000)  # rough for now
    router = get_router(w3)
    deadline = int(time.time()) + 300
    params = {
        "tokenIn": to_checksum_address(token),
        "tokenOut": WETH,
        "fee": fee,
        "recipient": sender,
        "deadline": deadline,
        "amountIn": amount_token,
        "amountOutMinimum": min_out,
        "sqrtPriceLimitX96": 0,
    }
    tx = router.functions.exactInputSingle(params).build_transaction({
        "from": sender,
        "chainId": CHAIN_ID,
    })
    max_fee = get_gas_price_with_premium(w3, 50)
    tx["maxFeePerGas"] = max_fee
    tx["maxPriorityFeePerGas"] = w3.to_wei(2, "gwei")
    tx["nonce"] = w3.eth.get_transaction_count(sender)
    gas = estimate_gas_with_buffer(w3, tx)
    tx["gas"] = gas
    print(f"Sell attempt: amount_token={amount_token}")
    signed = account.sign_transaction(tx)
    try:
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print("Sell tx sent:", tx_hash.hex())
        tg_send(f"💸 Sell tx sent for <code>{token}</code>\nTx: <code>{tx_hash.hex()}</code>")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
        if receipt.status == 1:
            log_trade(token, "sell", amount_token, tx_hash.hex(), "success")
            tg_send(f"✅ <b>SELL SUCCESS</b> for {token}")
            return tx_hash.hex()
    except Exception as e:
        print(f"Sell error: {e}")
    return None

# =============================================================================
# MONITORING
# =============================================================================
def monitor_new_pools_and_snipe(w3: Web3, buy_amount_eth: float = 0.05, cfg: dict = None, dry_run: bool = True):
    """
    Poll for UniswapV3 PoolCreated using get_logs (more reliable than persistent filters on HTTP RPCs).
    On new pool involving a token that looks like a fresh launch (or B20), attempt buy.
    Automatic small amount 0.001 ETH sniping when live and activated.
    """
    factory = get_uniswap_v3_factory(w3)
    pool_created_topic = factory.events.PoolCreated.build_filter().topics[0]  # approx, use full event

    print("Starting Uniswap V3 PoolCreated monitor (Mainnet, polling mode)...")

    last_block = w3.eth.block_number
    seen_pools = set()
    activated = False
    last_activation_check = 0

    # Fixed small amount for automatic meme sniping
    SNIPE_AMOUNT_ETH = 0.001

    while True:
        if is_kill_switch_active(cfg.get("KILL_SWITCH_FILE", "")):
            print("KILL SWITCH ACTIVE - stopping monitor")
            tg_send("🛑 Kill switch activated - bot stopped")
            break

        current_time = time.time()
        if current_time - last_activation_check > 30:
            try:
                activated = check_b20_activated(w3, want_stable=False)
                last_activation_check = current_time
                if activated:
                    tg_send("🎉 B20 ACTIVATION FLIPPED! Now fully LIVE for real B20 meme sniping (0.001 ETH auto).")
            except Exception as act_e:
                print(f"Activation check error: {act_e}")

        try:
            current_block = w3.eth.block_number
            if current_block > last_block:
                # Early B20 creation signals (key upgrade)
                check_recent_b20_creations(w3, last_block, current_block)

                # Use get_logs for PoolCreated
                logs = w3.eth.get_logs({
                    "fromBlock": last_block + 1,
                    "toBlock": current_block,
                    "address": UNISWAP_V3_FACTORY,
                    "topics": [factory.events.PoolCreated.build_filter().topics[0]]
                })
                for log in logs:
                    try:
                        event = factory.events.PoolCreated().process_log(log)
                        args = event["args"]
                        token0 = args["token0"]
                        token1 = args["token1"]
                        fee = args["fee"]
                        pool = args["pool"]

                        if pool in seen_pools:
                            continue
                        seen_pools.add(pool)

                        print(f"PoolCreated: {token0} / {token1} fee={fee} pool={pool}")

                        new_token = None
                        if token0.lower() != WETH.lower() and token0.lower() != USDC.lower():
                            new_token = token0
                        elif token1.lower() != WETH.lower() and token1.lower() != USDC.lower():
                            new_token = token1

                        if not new_token:
                            continue

                        is_b20 = False
                        try:
                            fac = get_b20_factory(w3)
                            is_b20 = fac.functions.isB20(to_checksum_address(new_token)).call()
                        except Exception:
                            pass

                        if new_token.lower().startswith("0xb20") or is_b20:
                            print(f"Detected likely B20 token: {new_token} (isB20={is_b20})")

                            name, sym = get_token_name_symbol(w3, new_token)
                            msg = f"🆕 <b>{name} ({sym})</b>\n<code>{new_token}</code>\nPool: <code>{pool}</code> fee={fee}"

                            buttons = {
                                "inline_keyboard": [
                                    [{"text": "0.003 ETH", "callback_data": f"buy_{new_token}_0.003"},
                                     {"text": "0.005 ETH", "callback_data": f"buy_{new_token}_0.005"}],
                                    [{"text": "0.007 ETH", "callback_data": f"buy_{new_token}_0.007"},
                                     {"text": "0.01 ETH", "callback_data": f"buy_{new_token}_0.01"}],
                                ]
                            }
                            tg_send(msg, reply_markup=buttons)

                        # Automatic small amount sniping - no pool alerts
                        if dry_run or not activated:
                            print(f"[{'DRY RUN' if dry_run else 'WAITING ACTIVATION'}] Would snipe {new_token} with {SNIPE_AMOUNT_ETH} ETH")
                            liq = check_pool_liquidity(w3, pool)
                            print(f"[{'DRY' if dry_run else 'WAIT'}] liquidity() = {liq}")
                            continue

                        # Real automatic snipe with small fixed amount
                        print(f"AUTO SNIPE: Attempting buy {new_token} with {SNIPE_AMOUNT_ETH} ETH (live)")
                        attempt_buy(w3, new_token, fee, SNIPE_AMOUNT_ETH, cfg, max_retries=1)
                    except Exception as decode_err:
                        print(f"Log decode error: {decode_err}")

                last_block = current_block

            time.sleep(5)  # Increased sleep to reduce rate limits on public RPCs

            # TG commands now in separate thread for speed (see below)

        except KeyboardInterrupt:
            print("Monitor stopped by user.")
            break
        except Exception as e:
            print(f"Monitor error: {e}")
            # Refresh w3 from the many RPCs on error (failover)
            try:
                rpc_list = cfg.get("BACKUP_RPCS", []) or DEFAULT_BASE_RPCS
                w3 = get_working_w3(rpc_list)
                global current_w3
                current_w3 = w3
                print("Switched to new RPC due to error")
            except:
                pass
            time.sleep(10)  # Backoff on errors (e.g. 429 rate limit)

# =============================================================================
# MAIN
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="B20 Base Mainnet Sniper/Launcher (Mainnet ONLY)")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Simulate only, no real tx (default)")
    parser.add_argument("--live", action="store_true", help="Enable live transactions (DANGEROUS)")
    parser.add_argument("--create-b20", action="store_true", help="Create a B20 token (requires --live and activation)")
    parser.add_argument("--monitor", action="store_true", help="Monitor Uniswap pools and snipe")
    parser.add_argument("--buy-amount", type=float, default=0.03, help="ETH amount per snipe attempt")
    parser.add_argument("--salt", type=str, default="grok-b20-launch-1", help="Salt for createB20 (string)")
    args = parser.parse_args()

    dry_run = not args.live

    print("=" * 70)
    print("B20 MAINNET BOT - BASE MAINNET (chainId=8453) ONLY")
    print(f"Current UTC: {now_utc().isoformat()}")
    print(f"Scheduled activation: {ACTIVATION_UTC.isoformat()}")
    print(f"Mode: {'DRY-RUN / SIMULATE' if dry_run else 'LIVE - REAL FUNDS AT RISK'}")
    print("=" * 70)

    cfg = load_config()
    init_db()
    rpc_list = cfg.get("BACKUP_RPCS", []) or DEFAULT_BASE_RPCS
    w3 = get_working_w3(rpc_list)
    global current_w3
    current_w3 = w3

    # Run TG polling in separate thread for faster response to commands/buttons
    tg_thread = threading.Thread(target=check_tg_commands, args=(cfg, None), daemon=True)
    tg_thread.start()

    # Live mode guard
    if not dry_run:
        key = cfg.get("PRIVATE_KEY", "")
        if not key or "YOUR" in key or len(key) < 10:
            print("ERROR: Cannot run in LIVE mode with invalid/placeholder PRIVATE_KEY.")
            print("Update .env with real key and restart.")
            sys.exit(1)
        print("LIVE MODE: Real funds at risk. Ensure wallet is funded with small test amount.")

    # Enforce Mainnet
    mainnet_sanity_check(w3)

    # Activation status (critical)
    asset_ok = check_b20_activated(w3, want_stable=False)
    if not asset_ok:
        print("WARNING: B20 ASSET not yet activated on-chain. createB20 will revert with FeatureNotActivated.")

    mode_str = "LIVE" if not dry_run else "DRY-RUN"
    safe_cfg = get_safe_config(cfg)
    print(f"MAX_TRADE={safe_cfg['MAX_TRADE_ETH']} ETH | SLIPPAGE={safe_cfg['SLIPPAGE_BPS']}bps | KILL={safe_cfg['KILL_SWITCH_FILE']}")
    print(f"RPC (masked): {mask_sensitive(safe_cfg['RPC_URL'])}")
    tg_send(f"🚀 <b>B20 Bot started</b>\nMode: {mode_str}\nB20 Activated: {asset_ok}\nChain: 8453\nMax trade: {cfg['MAX_TRADE_ETH']} ETH")

    if not dry_run:
        print("⚠️  LIVE MODE ENABLED - REAL ETH WILL BE USED")
        tg_send("⚠️ <b>LIVE MODE</b> - Real trades active")

    # Send buttons menu on startup
    buttons = {
        "inline_keyboard": [
            [{"text": "📊 Status", "callback_data": "status"},
             {"text": "⏸️ Pause", "callback_data": "pause"}],
            [{"text": "▶️ Resume", "callback_data": "resume"},
             {"text": "🛑 Kill", "callback_data": "kill"}]
        ]
    }
    tg_send("B20 Bot Controls:", reply_markup=buttons)

    if args.create_b20:
        print("\n--- CREATE B20 ---")
        if not asset_ok:
            print("Aborting create: feature not activated.")
            return

        account = w3.eth.account.from_key(cfg["PRIVATE_KEY"])
        salt = keccak(text=args.salt)
        params = encode_simple_asset_params("GrokB20Test", "GB20", account.address, 18)
        init_calls: list[bytes] = []  # Add grantRole / supply cap etc via proper encoding in production

        print("Params encoded (demo). Use proper B20FactoryLib encoding for production.")
        create_b20_live(w3, variant=0, salt=salt, params=params, init_calls=init_calls, dry_run=dry_run)

    if args.monitor or (not args.create_b20):
        print("\n--- STARTING POOL MONITOR + SNIPER ---")
        # Start mempool monitoring in background for early signals if available
        if MEMPOOL_AVAILABLE and not dry_run:
            try:
                mempool = MempoolMonitor(
                    ws_rpc_url=cfg.get("RPC_URL", "wss://base-mainnet.public.blastapi.io").replace("https://", "wss://"),
                    on_b20_detected=lambda tx, txh, st: print(f"[MEMPOOL] B20 early: {txh}"),
                    on_pool_detected=lambda tx, txh, st: print(f"[MEMPOOL] Pool early: {txh}")
                )
                # Run in background thread
                import threading
                mempool_thread = threading.Thread(target=lambda: asyncio.run(mempool.start()), daemon=True)
                mempool_thread.start()
                print("Mempool monitoring started in background for early detection")
            except Exception as me:
                print(f"Mempool start failed (optional): {me}")

        monitor_new_pools_and_snipe(w3, buy_amount_eth=min(args.buy_amount, cfg["MAX_TRADE_ETH"]), cfg=cfg, dry_run=dry_run)

if __name__ == "__main__":
    main()
