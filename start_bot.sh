#!/bin/bash
cd /home/ubuntu/b20-bot
source venv/bin/activate
exec python -u b20_mainnet_sniper.py --monitor 2>&1 | tee -a bot.log
