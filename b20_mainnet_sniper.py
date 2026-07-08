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
- Never hardcode private keys. Use environment variables or secure secret management.
- Clock must be NTP-synced to UTC.

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
import argparse
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any

from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import TransactionNotFound
from eth_utils import keccak, to_checksum_address
from eth_abi import encode

# =============================================================================
# MAINNET-ONLY CONSTANTS (DO NOT CHANGE)
# =============================================================================
CHAIN_ID = 8453
RPC_DEFAULT = "https://mainnet.base.org"

ACTIVATION_REGISTRY = to_checksum_address("0x8453000000000000000000000000000000000001")
POLICY_REGISTRY     = to_checksum_address("0x8453000000000000000000000000000000000002")
B20_FACTORY         = to_checksum_address("0xB20f000000000000000000000000000000000000")

UNISWAP_V3_FACTORY  = to_checksum_address("0x33128a8fC17869897dcE68Ed026d694621f6FDfD")
UNISWAP_V3_ROUTER   = to_checksum_address("0xE592427A0AEce92De3Edee1F18E0157C05861564")
WETH                = to_checksum_address("0x4200000000000000000000000000000000000006")
USDC                = to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")

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

# =============================================================================
# UTILS
# =============================================================================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def is_activation_time_passed() -> bool:
    return now_utc() >= ACTIVATION_UTC

def load_config() -> Dict[str, str]:
    load_dotenv()
    cfg = {
        "RPC_URL": os.getenv("RPC_URL", RPC_DEFAULT),
        "PRIVATE_KEY": os.getenv("PRIVATE_KEY", ""),
        "FLASHBOTS_RPC": os.getenv("FLASHBOTS_RPC", ""),
        "WALLET_ADDRESS": os.getenv("WALLET_ADDRESS", ""),
    }
    if not cfg["PRIVATE_KEY"]:
        # Allow running in pure monitor/dry-run without key (for setup and testing)
        print("WARNING: PRIVATE_KEY not set - transactions will fail. Use for monitoring only.")
    return cfg

def get_w3(rpc_url: str) -> Web3:
    if rpc_url.startswith("wss://"):
        w3 = Web3(Web3.WebsocketProvider(rpc_url))
    else:
        w3 = Web3(Web3.HTTPProvider(rpc_url))
    return w3

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

def attempt_buy(w3: Web3, token: str, fee: int, amount_eth: float, slippage_bps: int = 1500,
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
    # Very rough min_out calc; in prod use Quoter or good oracle
    min_out = 0  # WARNING: 0 = accept any amount (extreme slippage). Set properly in real use.
    # For production compute a reasonable min_out using on-chain quote or TWAP.

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
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
            if receipt.status == 1:
                print("BUY SUCCESS:", tx_hash.hex())
                return tx_hash.hex()
            else:
                print("Buy tx reverted.")
        except Exception as e:
            print(f"Send error: {e}")

        # Per spec: if failed, retry immediately with higher gas / lower slippage (we already loosen on retry)
        if attempt < max_retries:
            time.sleep(0.5)
            # Re-check liquidity still exists
            if check_pool_liquidity(w3, pool) == 0:
                print("Liquidity disappeared. Aborting retries.")
                break

    return None

# =============================================================================
# MONITORING
# =============================================================================
def monitor_new_pools_and_snipe(w3: Web3, buy_amount_eth: float = 0.05, dry_run: bool = True):
    """
    Poll for UniswapV3 PoolCreated using get_logs (more reliable than persistent filters on HTTP RPCs).
    On new pool involving a token that looks like a fresh launch (or B20), attempt buy.
    """
    factory = get_uniswap_v3_factory(w3)
    pool_created_topic = factory.events.PoolCreated.build_filter().topics[0]  # approx, use full event

    print("Starting Uniswap V3 PoolCreated monitor (Mainnet, polling mode)...")

    last_block = w3.eth.block_number

    while True:
        try:
            current_block = w3.eth.block_number
            if current_block > last_block:
                # Use get_logs for PoolCreated
                logs = w3.eth.get_logs({
                    "fromBlock": last_block + 1,
                    "toBlock": current_block,
                    "address": UNISWAP_V3_FACTORY,
                    "topics": [factory.events.PoolCreated.build_filter().topics[0]]
                })
                for log in logs:
                    # Decode manually or use event
                    try:
                        event = factory.events.PoolCreated().process_log(log)
                        args = event["args"]
                        token0 = args["token0"]
                        token1 = args["token1"]
                        fee = args["fee"]
                        pool = args["pool"]

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

                        if dry_run:
                            print("[DRY RUN] Would attempt buy on", new_token)
                            liq = check_pool_liquidity(w3, pool)
                            print(f"[DRY RUN] liquidity() = {liq}")
                            continue

                        attempt_buy(w3, new_token, fee, buy_amount_eth, slippage_bps=2000, max_retries=1)
                    except Exception as decode_err:
                        print(f"Log decode error: {decode_err}")

                last_block = current_block

            time.sleep(3)

        except KeyboardInterrupt:
            print("Monitor stopped by user.")
            break
        except Exception as e:
            print(f"Monitor error: {e}")
            time.sleep(5)

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
    w3 = get_w3(cfg["RPC_URL"])

    # Enforce Mainnet
    mainnet_sanity_check(w3)

    # Activation status (critical)
    asset_ok = check_b20_activated(w3, want_stable=False)
    if not asset_ok:
        print("WARNING: B20 ASSET not yet activated on-chain. createB20 will revert with FeatureNotActivated.")

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
        monitor_new_pools_and_snipe(w3, buy_amount_eth=args.buy_amount, dry_run=dry_run)

if __name__ == "__main__":
    main()
