#!/bin/bash
# Deployment script for Mempool Monitoring Upgrade (Phase 1 #2)

set -e

echo "=== B20 Bot Mempool Upgrade Deployment ==="

cd /home/ubuntu/b20-bot

echo "Pulling latest code..."
git fetch origin
git checkout nftboy07-implement-all-upgrades
git pull origin nftboy07-implement-all-upgrades

echo "Installing dependencies..."
source venv/bin/activate
pip install -r requirements.txt

echo "Restarting bot service..."
sudo systemctl restart b20-bot

echo "Verifying..."
sleep 3
sudo systemctl status b20-bot --no-pager | head -5

echo "Testing modules..."
python3 -c "
from mempool_monitor import MempoolMonitor
from early_detection import EarlyDetectionEngine
print('✅ Mempool modules imported successfully')
"

echo "=== Deployment Complete ==="
echo "Monitor logs with: sudo journalctl -u b20-bot -f"
