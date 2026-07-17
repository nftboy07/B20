import unittest
from unittest.mock import MagicMock, patch
import time
import os

# Set environment variable for test
os.environ["SNIPE_AMOUNT_ETH"] = "0.015"

# Import target elements
import b20_mainnet_sniper

class TestRetryQueue(unittest.TestCase):
    def setUp(self):
        b20_mainnet_sniper.PENDING_POOLS_TO_RETRY.clear()
        b20_mainnet_sniper.ACTIVE_POSITIONS.clear()

    @patch("b20_mainnet_sniper.check_token_safety")
    @patch("b20_mainnet_sniper.attempt_buy")
    def test_retry_queue_flow(self, mock_attempt_buy, mock_check_token_safety):
        w3_mock = MagicMock()
        cfg = {"MIN_LIQUIDITY_ETH": 5.0}
        
        # Test Case 1: First check fails with "Low liquidity"
        mock_check_token_safety.return_value = (False, "Low liquidity: 0")
        
        token = "0xTokenAddressEndingIn01"
        pool = "0xPoolAddress"
        fee = 3000
        
        # Simulate check in main loop
        safe, reason = b20_mainnet_sniper.check_token_safety(w3_mock, token, 5.0)
        self.assertFalse(safe)
        
        # Add to retry queue manually simulating monitor_new_pools_and_snipe logic
        if "Low liquidity" in reason:
            b20_mainnet_sniper.PENDING_POOLS_TO_RETRY[token] = {
                'pool': pool,
                'fee': fee,
                'timestamp': time.time(),
                'attempts': 0
            }
            
        self.assertIn(token, b20_mainnet_sniper.PENDING_POOLS_TO_RETRY)
        self.assertEqual(b20_mainnet_sniper.PENDING_POOLS_TO_RETRY[token]['attempts'], 0)
        
        # Test Case 2: Process retry queue, still failing
        mock_check_token_safety.return_value = (False, "Low liquidity: 0")
        
        # Simulating processing block
        retry_tokens = list(b20_mainnet_sniper.PENDING_POOLS_TO_RETRY.keys())
        for t in retry_tokens:
            item = b20_mainnet_sniper.PENDING_POOLS_TO_RETRY[t]
            item['attempts'] += 1
            safe_ret, reason_ret = b20_mainnet_sniper.check_token_safety(w3_mock, t, 5.0)
            self.assertFalse(safe_ret)
            
        self.assertEqual(b20_mainnet_sniper.PENDING_POOLS_TO_RETRY[token]['attempts'], 1)
        mock_attempt_buy.assert_not_called()
        
        # Test Case 3: Process retry queue, now passing safety
        mock_check_token_safety.return_value = (True, "Safe")
        
        retry_tokens = list(b20_mainnet_sniper.PENDING_POOLS_TO_RETRY.keys())
        for t in retry_tokens:
            item = b20_mainnet_sniper.PENDING_POOLS_TO_RETRY[t]
            item['attempts'] += 1
            safe_ret, reason_ret = b20_mainnet_sniper.check_token_safety(w3_mock, t, 5.0)
            if safe_ret:
                b20_mainnet_sniper.attempt_buy(w3_mock, t, item['fee'], 0.015, cfg, max_retries=1)
                b20_mainnet_sniper.ACTIVE_POSITIONS[t] = 0.015
                b20_mainnet_sniper.PENDING_POOLS_TO_RETRY.pop(t, None)
                
        self.assertNotIn(token, b20_mainnet_sniper.PENDING_POOLS_TO_RETRY)
        self.assertIn(token, b20_mainnet_sniper.ACTIVE_POSITIONS)
        mock_attempt_buy.assert_called_once_with(w3_mock, token, fee, 0.015, cfg, max_retries=1)

    def test_fee_tier_lists(self):
        # Verify fee tiers lists contain 100
        self.assertIn(100, b20_mainnet_sniper.UNISWAP_FEE_TIERS)

if __name__ == "__main__":
    unittest.main()
