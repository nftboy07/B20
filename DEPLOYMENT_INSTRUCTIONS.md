# Deployment Instructions for Mempool Monitoring

## Option 1: Automatic (within 60 min)
The cron job will handle it.

## Option 2: Manual
ssh ...
bash deploy_mempool_upgrade.sh

## Option 3: Git Pull
ssh ...
cd /home/ubuntu/b20-bot
git pull ...
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart b20-bot
