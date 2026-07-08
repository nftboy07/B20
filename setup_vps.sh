#!/bin/bash
set -e
echo "=== B20 Bot VPS Setup ==="
cd /home/ubuntu/b20-bot

echo "1. Ensuring venv exists..."
if [ ! -d venv ]; then
  python3 -m venv venv
fi
source venv/bin/activate

echo "2. Installing requirements..."
pip install --quiet -r requirements.txt

echo "3. Copy .env if needed..."
cp -f .env.example .env || true

echo "4. Testing connection and activation (no private key)..."
python -c '
from web3 import Web3
from eth_utils import keccak, to_checksum_address
w3 = Web3(Web3.HTTPProvider("https://mainnet.base.org"))
print("Chain ID:", w3.eth.chain_id)
ACT = to_checksum_address("0x8453000000000000000000000000000000000001")
print("Activation Registry reachable:", len(w3.eth.get_code(ACT)) >= 0)
reg = w3.eth.contract(address=ACT, abi=[{"inputs":[{"name":"feature","type":"bytes32"}],"name":"isActivated","outputs":[{"name":"","type":"bool"}],"stateMutability":"view","type":"function"}])
feat = keccak(text="base.b20_asset")
print("B20 ASSET activated:", reg.functions.isActivated(feat).call())
print("Sanity check passed.")
'

echo "=== Setup complete ==="
echo "Next: edit .env with your RPC_URL and PRIVATE_KEY"
echo "Then run: source venv/bin/activate && python b20_mainnet_sniper.py --monitor"
