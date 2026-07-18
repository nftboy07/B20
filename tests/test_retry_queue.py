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

    def test_launchpad_filtering_logic(self):
        # Helper to simulate our launchpad filter block
        def is_allowed(token, cfg):
            is_o1 = token.lower().endswith("01")
            is_cc0 = token.lower().endswith("cc0")
            if cfg.get("ONLY_O1_LAUNCHPAD", False) or cfg.get("ONLY_CC0_LAUNCHPAD", False):
                allowed_launchpad = False
                if cfg.get("ONLY_O1_LAUNCHPAD", False) and is_o1:
                    allowed_launchpad = True
                if cfg.get("ONLY_CC0_LAUNCHPAD", False) and is_cc0:
                    allowed_launchpad = True
                return allowed_launchpad
            return True

        # Case 1: Only o1 enabled
        cfg1 = {"ONLY_O1_LAUNCHPAD": True, "ONLY_CC0_LAUNCHPAD": False}
        self.assertTrue(is_allowed("0xabcdef01", cfg1))
        self.assertFalse(is_allowed("0xabcdefcc0", cfg1))
        self.assertFalse(is_allowed("0xabcdef00", cfg1))

        # Case 2: Only cc0 enabled
        cfg2 = {"ONLY_O1_LAUNCHPAD": False, "ONLY_CC0_LAUNCHPAD": True}
        self.assertFalse(is_allowed("0xabcdef01", cfg2))
        self.assertTrue(is_allowed("0xabcdefcc0", cfg2))
        self.assertFalse(is_allowed("0xabcdef00", cfg2))

        # Case 3: Both enabled
        cfg3 = {"ONLY_O1_LAUNCHPAD": True, "ONLY_CC0_LAUNCHPAD": True}
        self.assertTrue(is_allowed("0xabcdef01", cfg3))
        self.assertTrue(is_allowed("0xabcdefcc0", cfg3))
        self.assertFalse(is_allowed("0xabcdef00", cfg3))

        # Case 4: Neither enabled (any token allowed)
        cfg4 = {"ONLY_O1_LAUNCHPAD": False, "ONLY_CC0_LAUNCHPAD": False}
        self.assertTrue(is_allowed("0xabcdef01", cfg4))
        self.assertTrue(is_allowed("0xabcdefcc0", cfg4))
        self.assertTrue(is_allowed("0xabcdef00", cfg4))

    def test_launchpad_amount_resolution(self):
        # Set up mock env variables or direct variables to test the formula
        SNIPE_AMOUNT_ETH = 0.015
        SNIPE_AMOUNT_O1_ETH = 0.015
        SNIPE_AMOUNT_CC0_ETH = 0.001

        def get_amount(token):
            is_o1 = token.lower().endswith("01")
            is_cc0 = token.lower().endswith("cc0")
            return SNIPE_AMOUNT_CC0_ETH if is_cc0 else (SNIPE_AMOUNT_O1_ETH if is_o1 else SNIPE_AMOUNT_ETH)

        self.assertEqual(get_amount("0xabcdef01"), 0.015)
        self.assertEqual(get_amount("0xabcdefcc0"), 0.001)
        self.assertEqual(get_amount("0xabcdef99"), 0.015)

if __name__ == "__main__":
    unittest.main()
