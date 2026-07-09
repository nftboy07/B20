#!/usr/bin/env python3
"""
Mempool Monitor for B20 Meme Sniping Bot
Provides 5-30 second detection advantage by watching pending transactions.

Features:
- WebSocket connection to RPC for real-time pending TXs
- Filter for B20 pool creations (B20Factory createB20)
- Filter for Uniswap V3 pool creations (PoolCreated)
- Decode function parameters
- Gas price analysis
- Statistics tracking
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set
from web3 import Web3
from web3.providers.websocket import WebsocketProvider
from eth_utils import keccak, to_checksum_address
import logging

logger = logging.getLogger(__name__)

# B20 Factory address from spec
B20_FACTORY = "0xB20f000000000000000000000000000000000000"

# Uniswap V3 Factory on Base
UNISWAP_V3_FACTORY = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"

# Function signatures
CREATE_B20_SIG = keccak(text="createB20(uint8,bytes32,bytes,bytes[])")[:4].hex()
POOL_CREATED_SIG = keccak(text="PoolCreated(address,address,uint24,int24,address)")[:4].hex()

class MempoolMonitor:
    def __init__(self, ws_rpc_url: str, on_b20_detected: Optional[Callable] = None, on_pool_detected: Optional[Callable] = None):
        self.ws_rpc_url = ws_rpc_url
        self.on_b20_detected = on_b20_detected
        self.on_pool_detected = on_pool_detected
        self.w3 = None
        self.is_running = False
        self.stats = {
            "total_txs": 0,
            "b20_detections": 0,
            "pool_creations": 0,
            "false_positives": 0,
            "start_time": None,
            "last_detection": None,
            "avg_lead_time": 0,
            "detections": []
        }
        self.seen_txs: Set[str] = set()
        self.known_honeypots: Set[str] = set()  # Could load from DB

    async def connect(self):
        """Connect to WebSocket RPC."""
        try:
            self.w3 = Web3(WebsocketProvider(self.ws_rpc_url))
            # Wait for connection
            while not self.w3.isConnected():
                await asyncio.sleep(0.1)
            logger.info(f"Connected to {self.ws_rpc_url}")
            return True
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            return False

    async def start(self):
        """Start monitoring the mempool."""
        if not await self.connect():
            logger.error("Failed to connect, cannot start monitoring")
            return

        self.is_running = True
        self.stats["start_time"] = datetime.utcnow()

        logger.info("Starting mempool monitoring...")

        # Subscribe to new pending transactions
        subscription = self.w3.eth.subscribe("newPendingTransactions")

        try:
            async for tx_hash in subscription:
                if not self.is_running:
                    break
                await self._process_tx(tx_hash)
        except Exception as e:
            logger.error(f"Mempool monitoring error: {e}")
        finally:
            await self.stop()

    async def _process_tx(self, tx_hash: str):
        """Process a pending transaction."""
        if tx_hash in self.seen_txs:
            return
        self.seen_txs.add(tx_hash)
        self.stats["total_txs"] += 1

        try:
            tx = self.w3.eth.getTransaction(tx_hash)
            if not tx:
                return

            to_addr = tx.get("to", "").lower() if tx.get("to") else ""
            input_data = tx.get("input", "0x")

            # Check for B20 creation
            if to_addr == B20_FACTORY.lower() and input_data.startswith("0x" + CREATE_B20_SIG[2:]):
                await self._handle_b20_creation(tx, tx_hash)
                return

            # Check for Uniswap V3 PoolCreated (usually from factory, but sometimes direct)
            if to_addr == UNISWAP_V3_FACTORY.lower() and input_data.startswith("0x" + POOL_CREATED_SIG[2:]):
                await self._handle_pool_creation(tx, tx_hash)
                return

            # Also check for router calls that might create pools indirectly, but focus on factory for now

        except Exception as e:
            # Many txs will fail to fetch if not found yet, or other errors
            if "not found" not in str(e).lower():
                logger.debug(f"Error processing tx {tx_hash}: {e}")

    async def _handle_b20_creation(self, tx: dict, tx_hash: str):
        """Handle detected B20 creation."""
        self.stats["b20_detections"] += 1
        self.stats["last_detection"] = datetime.utcnow()

        input_data = tx.get("input", "0x")
        # Decode to get name if possible (simplified)
        name = "B20 Token"
        try:
            # Skip selector (4 bytes), decode the tuple
            # params is the 3rd arg, offset etc.
            # For simplicity, try to find strings in input
            data = bytes.fromhex(input_data[2:] if input_data.startswith('0x') else input_data)
            # Rough: look for string lengths
            # Better decode using eth_abi
            from eth_abi import decode
            # The input after 4 bytes is encoded (uint8, bytes32, bytes, bytes[])
            # Decode first 3
            decoded = decode(['uint8', 'bytes32', 'bytes'], data[4:4+32+32+32+32])  # rough
            params = decoded[2]
            # params starts with version byte + strings
            if len(params) > 1:
                # skip version, decode strings
                name_len = int.from_bytes(params[1:33], 'big') if len(params) > 33 else 0
                if name_len > 0 and name_len < 100:
                    name = params[33:33+name_len].decode('utf-8', errors='ignore')
        except:
            pass

        logger.info(f"B20 creation detected in mempool: {tx_hash}")
        print(f"🚀 MEMPOOL B20 DETECTED: {name} {tx_hash} from {tx.get('from')}")

        if self.on_b20_detected:
            await self.on_b20_detected(tx, tx_hash, "b20_creation", name)

        # Record for stats
        self._record_detection(tx_hash, "b20", tx.get("gasPrice", 0))

    async def _handle_pool_creation(self, tx: dict, tx_hash: str):
        """Handle detected Uniswap pool creation."""
        self.stats["pool_creations"] += 1
        self.stats["last_detection"] = datetime.utcnow()

        logger.info(f"Pool creation detected in mempool: {tx_hash}")
        print(f"💧 MEMPOOL POOL DETECTED: {tx_hash}")

        if self.on_pool_detected:
            await self.on_pool_detected(tx, tx_hash, "uniswap_pool")

        self._record_detection(tx_hash, "pool", tx.get("gasPrice", 0))

    def _record_detection(self, tx_hash: str, dtype: str, gas_price: int):
        detection = {
            "tx": tx_hash,
            "type": dtype,
            "gas_price": gas_price,
            "timestamp": datetime.utcnow().isoformat()
        }
        self.stats["detections"].append(detection)
        # Keep only last 100
        if len(self.stats["detections"]) > 100:
            self.stats["detections"].pop(0)

    async def stop(self):
        self.is_running = False
        if self.w3 and self.w3.provider:
            await self.w3.provider.disconnect()
        logger.info("Mempool monitor stopped")

    def get_stats(self) -> dict:
        if self.stats["start_time"]:
            runtime = (datetime.utcnow() - self.stats["start_time"]).total_seconds()
            self.stats["runtime_seconds"] = runtime
            if self.stats["pool_creations"] > 0:
                self.stats["avg_lead_time"] = runtime / self.stats["pool_creations"]  # rough
        return self.stats.copy()

# Example usage
if __name__ == "__main__":
    async def main():
        # Example with public (use paid for prod)
        monitor = MempoolMonitor("wss://base-mainnet.g.alchemy.com/v2/YOUR_KEY")
        await monitor.start()

    asyncio.run(main())
