#!/usr/bin/env python3
"""
Mempool Monitor - Detect B20 PoolCreated Transactions Before They Hit Blockchain
==================================================================================
Watches Ethereum mempool for incoming B20 pool creation transactions
Detects transactions 5-30+ seconds before they're mined
Key advantage: Get in before other bots see the transaction
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Callable, Optional, List, Set
from dataclasses import dataclass

from web3 import Web3
try:
    from web3.providers import LegacyWebSocketProvider as WebsocketProvider
except ImportError:
    try:
        from web3.providers import WebsocketProvider
    except ImportError:
        from web3.providers import WebSocketProvider as WebsocketProvider
from eth_utils import to_checksum_address
from eth_abi import decode

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class PendingTransaction:
    """Mempool transaction data."""
    tx_hash: str
    from_address: str
    to_address: str
    data: str
    value: int
    gas: int
    gas_price: int
    timestamp: float
    function: str
    args: Dict
    

class MempoolMonitor:
    """Monitors Ethereum mempool for B20 pool creation transactions."""

    # Target contract addresses
    B20_FACTORY = to_checksum_address("0xB20f000000000000000000000000000000000000")
    UNISWAP_V3_FACTORY = to_checksum_address("0x33128a8fC17869897dcE68Ed026d694621f6FDfD")
    
    # Function signatures to watch
    B20_CREATE_TOKEN = "0x12345678"  # Placeholder - actual from contract
    UNISWAP_CREATE_POOL = "0x883164f5"  # createPool(address,address,uint24)
    
    # Mainnet RPC endpoints with WebSocket support
    WEBSOCKET_URLS = [
        "wss://base.publicnode.com",
        "wss://mainnet.base.org/ws",
    ]

    def __init__(self, rpc_url: str = None, db_manager=None, ws_rpc_url: str = None, on_b20_detected: Optional[Callable] = None, on_pool_detected: Optional[Callable] = None):
        """Initialize mempool monitor.
        
        Args:
            rpc_url: WebSocket RPC URL (if None, uses public endpoints)
            db_manager: Database manager for logging events
        """
        self.ws_rpc = rpc_url or ws_rpc_url or self.WEBSOCKET_URLS[0]
        self.db_manager = db_manager
        self.on_b20_detected = on_b20_detected
        self.on_pool_detected = on_pool_detected
        self.w3 = None
        self.pending_txs: Set[str] = set()
        self.is_running = False
        self.callbacks: List[Callable] = []
        self.stats = {
            'txs_seen': 0,
            'b20_txs': 0,
            'pool_creations': 0,
            'false_positives': 0,
            'avg_time_to_mine': 0,
        }

    async def initialize(self):
        """Connect to WebSocket RPC."""
        logger.info(f"🔗 Connecting to WebSocket RPC: {self.ws_rpc[:50]}...")
        
        try:
            provider = WebsocketProvider(self.ws_rpc)
            self.w3 = Web3(provider)
            
            if not self.w3.is_connected():
                logger.error("❌ WebSocket connection failed")
                raise Exception("Cannot connect to WebSocket RPC")
            
            logger.info("✅ WebSocket connected successfully")
            
        except Exception as e:
            logger.error(f"❌ Connection error: {e}")
            raise

    def register_callback(self, callback: Callable):
        """Register callback for when tx is detected.
        
        Args:
            callback: async function(tx_data, function, decoded_args)
        """
        self.callbacks.append(callback)

    async def watch_pending_transactions(self):
        """Watch for pending transactions in mempool."""
        logger.info("👀 Watching mempool for pending transactions...")
        
        try:
            # Create filter for pending transactions
            filter_obj = self.w3.eth.filter('pending')
            
            while self.is_running:
                try:
                    # Get pending transaction hashes
                    tx_hashes = filter_obj.get_new_entries()
                    
                    for tx_hash in tx_hashes:
                        await self._process_pending_tx(tx_hash.hex())
                    
                    await asyncio.sleep(0.1)  # Non-blocking wait
                    
                except Exception as e:
                    logger.error(f"⚠️ Error in mempool watch: {e}")
                    await asyncio.sleep(1)
        
        except Exception as e:
            logger.error(f"❌ Critical error in mempool watcher: {e}")

    async def _process_pending_tx(self, tx_hash: str):
        """Process single pending transaction."""
        if tx_hash in self.pending_txs:
            return  # Already processed
        
        self.pending_txs.add(tx_hash)
        self.stats['txs_seen'] += 1
        
        try:
            # Get transaction details
            tx = self.w3.eth.get_transaction(tx_hash)
            
            # Check if it's to a target contract
            to_addr = tx.get('to')
            data = tx.get('input', '0x')
            
            if not to_addr:
                return  # Contract creation, skip for now
            
            to_addr = to_checksum_address(to_addr)
            
            # Check if target is our factory
            if to_addr not in [self.B20_FACTORY, self.UNISWAP_V3_FACTORY]:
                return
            
            # Decode function signature
            func_sig = data[:10] if len(data) >= 10 else None
            
            # Handle B20 creations
            if to_addr == self.B20_FACTORY:
                await self._handle_b20_tx(tx_hash, tx, data, func_sig)
            
            # Handle Uniswap pool creations
            elif to_addr == self.UNISWAP_V3_FACTORY and func_sig == "0x883164f5":
                await self._handle_pool_creation_tx(tx_hash, tx, data)
        
        except Exception as e:
            logger.debug(f"Error processing tx {tx_hash[:8]}: {e}")

    async def _handle_b20_tx(self, tx_hash: str, tx: Dict, data: str, func_sig: str):
        """Handle B20 factory transaction."""
        self.stats['b20_txs'] += 1
        
        logger.info(f"🆕 Detected B20 transaction: {tx_hash[:8]}...")
        logger.info(f"   From: {tx.get('from')[:8]}...")
        logger.info(f"   Gas Price: {tx.get('gasPrice') / 1e9:.2f} gwei")
        logger.info(f"   Function: {func_sig}")
        
        # Trigger legacy callback
        if self.on_b20_detected:
            try:
                compat_tx = {
                    'to': self.B20_FACTORY,
                    'from': tx.get('from'),
                    'input': data,
                    'gasPrice': tx.get('gasPrice')
                }
                if asyncio.iscoroutinefunction(self.on_b20_detected):
                    await self.on_b20_detected(compat_tx, tx_hash, 'pending')
                else:
                    self.on_b20_detected(compat_tx, tx_hash, 'pending')
            except Exception as e:
                logger.error(f"Legacy B20 callback error: {e}")
                
        # Trigger callbacks
        for callback in self.callbacks:
            try:
                await callback('b20_pending_tx', {
                    'tx_hash': tx_hash,
                    'from': tx.get('from'),
                    'data': data,
                    'gas_price': tx.get('gasPrice'),
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                })
            except Exception as e:
                logger.error(f"Callback error: {e}")

    async def _handle_pool_creation_tx(self, tx_hash: str, tx: Dict, data: str):
        """Handle Uniswap V3 pool creation transaction."""
        self.stats['pool_creations'] += 1
        
        try:
            # Decode function parameters
            # createPool(address token0, address token1, uint24 fee)
            if len(data) >= 138:
                # Extract parameters
                params_data = data[10:]  # Remove function signature
                token0 = '0x' + params_data[24:64]
                token1 = '0x' + params_data[64:104]
                fee_hex = params_data[104:138]
                fee = int(fee_hex, 16)
                
                logger.info(f"🏊 Detected Uniswap V3 Pool Creation: {tx_hash[:8]}...")
                logger.info(f"   Token 0: {token0[:8]}...")
                logger.info(f"   Token 1: {token1[:8]}...")
                logger.info(f"   Fee Tier: {fee}")
                logger.info(f"   Gas Price: {tx.get('gasPrice') / 1e9:.2f} gwei")
                
                # Trigger legacy callback
                if self.on_pool_detected:
                    try:
                        compat_tx = {
                            'to': self.UNISWAP_V3_FACTORY,
                            'from': tx.get('from'),
                            'input': data,
                            'gasPrice': tx.get('gasPrice')
                        }
                        if asyncio.iscoroutinefunction(self.on_pool_detected):
                            await self.on_pool_detected(compat_tx, tx_hash, 'pending')
                        else:
                            self.on_pool_detected(compat_tx, tx_hash, 'pending')
                    except Exception as e:
                        logger.error(f"Legacy pool callback error: {e}")
                        
                # Trigger callbacks
                for callback in self.callbacks:
                    try:
                        await callback('pool_creation_pending', {
                            'tx_hash': tx_hash,
                            'token0': to_checksum_address(token0),
                            'token1': to_checksum_address(token1),
                            'fee': fee,
                            'from': tx.get('from'),
                            'gas_price': tx.get('gasPrice'),
                            'timestamp': datetime.now(timezone.utc).isoformat(),
                        })
                    except Exception as e:
                        logger.error(f"Callback error: {e}")
        
        except Exception as e:
            logger.debug(f"Error decoding pool creation: {e}")

    async def start(self):
        """Start monitoring mempool."""
        self.is_running = True
        logger.info("🚀 Starting mempool monitor...")
        
        try:
            await self.initialize()
            await self.watch_pending_transactions()
        
        except Exception as e:
            logger.error(f"❌ Mempool monitor crashed: {e}")
            self.is_running = False

    def stop(self):
        """Stop monitoring."""
        logger.info("⏹ Stopping mempool monitor...")
        self.is_running = False

    async def get_gas_trends(self) -> Dict:
        """Get current gas price trends from mempool.
        
        Returns:
            Dict with safe/standard/fast gas prices
        """
        try:
            latest_block = self.w3.eth.get_block('latest')
            base_fee = latest_block['baseFeePerGas']
            
            return {
                'base_fee_gwei': base_fee / 1e9,
                'safe_gwei': (base_fee * 1.2) / 1e9,
                'standard_gwei': (base_fee * 1.5) / 1e9,
                'fast_gwei': (base_fee * 2.0) / 1e9,
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }
        
        except Exception as e:
            logger.error(f"Error getting gas trends: {e}")
            return {}

    def get_stats(self) -> Dict:
        """Get monitoring statistics."""
        return {
            **self.stats,
            'is_running': self.is_running,
            'pending_txs_tracked': len(self.pending_txs),
        }


# Synchronous wrapper for non-async code
class MempoolMonitorSync:
    """Synchronous wrapper around async mempool monitor."""
    
    def __init__(self, rpc_url: str = None, db_manager=None):
        self.monitor = MempoolMonitor(rpc_url, db_manager)
        self.loop = None
        self.task = None

    def start(self, callback: Callable = None):
        """Start monitor in background thread."""
        import threading
        
        if callback:
            self.monitor.register_callback(callback)
        
        def run_async():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self.monitor.start())
            except Exception as e:
                logger.error(f"Monitor thread error: {e}")
        
        thread = threading.Thread(target=run_async, daemon=True)
        thread.start()
        logger.info("✅ Mempool monitor started in background")

    def stop(self):
        """Stop monitoring."""
        self.monitor.stop()
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)

    def get_stats(self) -> Dict:
        """Get stats."""
        return self.monitor.get_stats()


if __name__ == '__main__':
    """Test the mempool monitor."""
    import sys
    from dotenv import load_dotenv
    import os
    
    load_dotenv()
    
    async def test_callback(event_type: str, data: Dict):
        """Test callback."""
        logger.info(f"📡 Callback: {event_type}")
        logger.info(f"   Data: {json.dumps(data, indent=2, default=str)}")
    
    async def main():
        monitor = MempoolMonitor(
            rpc_url=os.getenv('WEBSOCKET_RPC'),
            db_manager=None
        )
        monitor.register_callback(test_callback)
        
        try:
            await monitor.start()
        except KeyboardInterrupt:
            logger.info("⏹ Shutting down...")
            monitor.stop()
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted")
