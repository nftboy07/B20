#!/bin/bash
#
# B20 Bot Auto-Update Service
# =======================================
# Automatically pulls latest code from Git and restarts bot
# Install as cron job or GitHub webhook receiver
#
# Usage:
#   1. Cron mode: Add to crontab (every hour)
#   2. Webhook mode: Run as systemd service listening for pushes
#

set -e

BOT_DIR="/home/ubuntu/b20-bot"
LOG_FILE="/home/ubuntu/b20-bot/logs/auto-update.log"
VENV_PATH="/home/ubuntu/b20-bot/venv"
BRANCH="nftboy07-implement-all-upgrades"
REPO="https://github.com/nftboy07/B20.git"

# Create log directory if needed
mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "=========================================="
log "🔄 Starting auto-update check..."
log "=========================================="

# Check if directory exists
if [ ! -d "$BOT_DIR" ]; then
    log "❌ ERROR: Bot directory not found: $BOT_DIR"
    exit 1
fi

cd "$BOT_DIR"
log "📂 Working directory: $(pwd)"

# ========== GIT PULL ==========
log "📥 Pulling latest code from $BRANCH..."

git fetch origin "$BRANCH" 2>&1 | tee -a "$LOG_FILE" || {
    log "❌ ERROR: git fetch failed"
    exit 1
}

# Check if there are changes
CHANGES=$(git diff origin/$BRANCH HEAD --stat | wc -l)

if [ "$CHANGES" -eq 0 ]; then
    log "✅ No changes found - already up to date"
    exit 0
fi

log "🔄 Found $CHANGES changed files - updating..."

# Pull changes
git pull origin "$BRANCH" 2>&1 | tee -a "$LOG_FILE" || {
    log "❌ ERROR: git pull failed"
    exit 1
}

log "✅ Code pulled successfully"

# ========== UPDATE DEPENDENCIES ==========
log "📦 Updating Python dependencies..."

if [ ! -d "$VENV_PATH" ]; then
    log "❌ ERROR: Virtual environment not found at $VENV_PATH"
    exit 1
fi

source "$VENV_PATH/bin/activate" || {
    log "❌ ERROR: Failed to activate venv"
    exit 1
}

pip install -q -r requirements.txt 2>&1 | tee -a "$LOG_FILE" || {
    log "⚠️  WARNING: pip install had issues but continuing..."
    # Don't exit here - pip install may have warnings
}

log "✅ Dependencies updated"

# ========== RESTART BOT ==========
log "🔄 Restarting b20-bot service..."

if sudo systemctl restart b20-bot 2>&1 | tee -a "$LOG_FILE"; then
    log "✅ Bot restarted successfully"
else
    log "⚠️  WARNING: systemctl restart returned non-zero exit"
fi

# ========== VERIFICATION ==========
sleep 3

if sudo systemctl is-active --quiet b20-bot; then
    log "✅ Bot is running and healthy!"
    log "=========================================="
    log "✅ AUTO-UPDATE COMPLETED SUCCESSFULLY"
    log "=========================================="
else
    log "❌ ERROR: Bot failed to start after update!"
    log "Check logs: sudo journalctl -u b20-bot -f"
    exit 1
fi

exit 0
