#!/usr/bin/env python3
"""
Early Detection Engine
Combines signals from mempool and blockchain for early B20 meme detection.

Provides confidence scoring and lead time calculation.
"""

import asyncio
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional
from collections import deque
import logging

logger = logging.getLogger(__name__)

class EarlyDetectionEngine:
    def __init__(self, on_detection: Optional[Callable] = None):
        self.on_detection = on_detection
        self.mempool_signals = deque(maxlen=100)
        self.blockchain_signals = deque(maxlen=100)
        self.stats = {
            "total_signals": 0,
            "b20_detections": 0,
            "avg_lead_time": 0,
            "false_positives": 0,
            "confidence_scores": []
        }
        self.callbacks = []
        if on_detection:
            self.callbacks.append(on_detection)

    def add_callback(self, callback: Callable):
        self.callbacks.append(callback)

    async def process_mempool_signal(self, tx: dict, tx_hash: str, signal_type: str):
        """Process signal from mempool monitor."""
        signal = {
            "source": "mempool",
            "tx": tx_hash,
            "type": signal_type,
            "timestamp": time.time(),
            "gas_price": tx.get("gasPrice", 0),
            "from": tx.get("from"),
            "to": tx.get("to")
        }
        self.mempool_signals.append(signal)
        self.stats["total_signals"] += 1

        # Combine with blockchain signals for confidence
        confidence = self._calculate_confidence(signal)
        lead_time = self._estimate_lead_time(signal)

        detection = {
            "signal": signal,
            "confidence": confidence,
            "estimated_lead_time": lead_time,
            "timestamp": datetime.utcnow().isoformat()
        }

        if confidence > 0.6:  # Threshold
            self.stats["b20_detections"] += 1
            self.stats["confidence_scores"].append(confidence)
            if lead_time:
                self.stats["avg_lead_time"] = (self.stats["avg_lead_time"] * (len(self.stats["confidence_scores"])-1) + lead_time) / len(self.stats["confidence_scores"])

            logger.info(f"Early detection: {tx_hash} confidence={confidence:.2f} lead={lead_time}s")
            for cb in self.callbacks:
                try:
                    await cb(detection) if asyncio.iscoroutinefunction(cb) else cb(detection)
                except Exception as e:
                    logger.error(f"Callback error: {e}")

    async def process_blockchain_signal(self, event: dict, signal_type: str):
        """Process signal from blockchain events (e.g. from main monitor)."""
        signal = {
            "source": "blockchain",
            "tx": event.get("transactionHash"),
            "type": signal_type,
            "timestamp": time.time(),
            "block": event.get("blockNumber"),
            "data": str(event)[:100]
        }
        self.blockchain_signals.append(signal)
        self.stats["total_signals"] += 1

        # Lower confidence for blockchain only
        confidence = 0.4
        self.stats["confidence_scores"].append(confidence)

    def _calculate_confidence(self, mempool_signal: dict) -> float:
        """Simple confidence based on gas, type, etc."""
        base = 0.5
        if mempool_signal["type"] == "b20_creation":
            base += 0.3
        if mempool_signal.get("gas_price", 0) > 1000000000:  # high gas
            base += 0.1
        # More heuristics...
        return min(1.0, base)

    def _estimate_lead_time(self, signal: dict) -> Optional[float]:
        """Estimate seconds until mined based on gas price etc."""
        # Very rough: higher gas = faster
        gas = signal.get("gas_price", 0)
        if gas > 2000000000:
            return 5.0
        elif gas > 1000000000:
            return 15.0
        return 25.0

    def get_stats(self) -> dict:
        if self.stats["confidence_scores"]:
            self.stats["avg_confidence"] = sum(self.stats["confidence_scores"]) / len(self.stats["confidence_scores"])
        return self.stats.copy()

# Example integration
if __name__ == "__main__":
    async def on_early_detect(detection):
        print(f"EARLY DETECT: {detection}")

    engine = EarlyDetectionEngine(on_early_detect)
    # Would be called from mempool_monitor
    print("Early detection engine ready")
