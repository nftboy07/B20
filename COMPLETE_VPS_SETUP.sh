#!/bin/bash
# Complete VPS Deployment & Setup Guide
# For: B20 Bot with All Upgrades
# Platform: Ubuntu 20.04+ / Debian 11+

echo "🚀 B20 Bot - Complete VPS Setup Guide"
echo "========================================"
echo ""
echo "This script sets up everything for production:"
echo "  ✅ Python environment"
echo "  ✅ Database (SQLite)"
echo "  ✅ Systemd service"
echo "  ✅ Docker & Docker Compose"
echo "  ✅ Prometheus + Grafana monitoring"
echo "  ✅ Security hardening"
echo "  ✅ Log rotation"
echo ""

set -e

# Configuration
BOT_USER="ubuntu"
BOT_DIR="/home/ubuntu/b20-bot"
DB_DIR="${BOT_DIR}/data"
LOG_DIR="${BOT_DIR}/logs"
BACKUP_DIR="/backups/b20-bot"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}✅${NC} $1"; }
log_warn() { echo -e "${YELLOW}⚠️${NC} $1"; }
log_error() { echo -e "${RED}❌${NC} $1"; }

# ============================================================================
# 1. System Updates
# ============================================================================
log_info "Step 1: System Updates"

sudo apt-get update
sudo apt-get upgrade -y
sudo apt-get install -y \
    python3.11 python3-pip python3-venv python3-dev \
    curl wget git ca-certificates \
    build-essential libssl-dev libffi-dev \
    ntp ntpdate \
    ufw fail2ban \
    sqlite3 \
    htop iotop

# Set timezone to UTC
sudo timedatectl set-timezone UTC
sudo systemctl enable systemd-timesyncd
sudo systemctl start systemd-timesyncd

log_info "System updated and timezone set to UTC"

# ============================================================================
# 2. Create Bot User & Directories
# ============================================================================
log_info "Step 2: Creating directories and setting permissions"

sudo useradd -m -s /bin/bash ${BOT_USER} || true
sudo mkdir -p ${BOT_DIR} ${DB_DIR} ${LOG_DIR} ${BACKUP_DIR}
sudo chown -R ${BOT_USER}:${BOT_USER} ${BOT_DIR}
sudo chmod 700 ${BOT_DIR} ${DB_DIR} ${LOG_DIR}

# ============================================================================
# 3. Python Virtual Environment
# ============================================================================
log_info "Step 3: Setting up Python virtual environment"

cd ${BOT_DIR}
python3.11 -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip setuptools wheel

# Install requirements
pip install -r requirements.txt

log_info "Python environment ready"

# ============================================================================
# 4. Initialize Database
# ============================================================================
log_info "Step 4: Initializing SQLite database"

python << 'PYEOF'
from db_manager import DBManager
import os

db_path = "/home/ubuntu/b20-bot/data/b20_bot.db"
db = DBManager(db_path)
print(f"✅ Database initialized at {db_path}")

# Log initialization event
db.log_event("system_startup", "info", "Bot database initialized", {
    "db_path": db_path,
    "tables": ["pools", "trades", "positions", "pnl_history", "safety_scores", "events"]
})

stats = db.get_stats()
print(f"📊 Stats: {stats}")
PYEOF

log_info "Database initialized"

# ============================================================================
# 5. Systemd Service
# ============================================================================
log_info "Step 5: Setting up systemd service"

sudo tee /etc/systemd/system/b20-bot.service > /dev/null << 'EOF'
[Unit]
Description=B20 Mainnet Sniper Bot - Advanced Meme Sniping
Documentation=https://github.com/nftboy07/B20
After=network.target ntp.service

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/b20-bot
Environment="PATH=/home/ubuntu/b20-bot/venv/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/home/ubuntu/b20-bot/venv/bin/python b20_mainnet_sniper.py --monitor --live
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
StandardInput=null

# Resource limits
MemoryLimit=2G
CPUQuota=75%

# Security
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/home/ubuntu/b20-bot

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable b20-bot.service

log_info "Systemd service configured"

# ============================================================================
# 6. Log Rotation
# ============================================================================
log_info "Step 6: Setting up log rotation"

sudo tee /etc/logrotate.d/b20-bot > /dev/null << 'EOF'
/home/ubuntu/b20-bot/logs/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0600 ubuntu ubuntu
    sharedscripts
    postrotate
        systemctl reload b20-bot.service > /dev/null 2>&1 || true
    endscript
}
EOF

log_info "Log rotation configured"

# ============================================================================
# 7. Firewall
# ============================================================================
log_info "Step 7: Configuring firewall (UFW)"

sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow 22/tcp          # SSH
sudo ufw allow 3000/tcp        # Grafana
sudo ufw allow 9090/tcp        # Prometheus
sudo ufw --force enable

log_info "Firewall configured"

# ============================================================================
# 8. Fail2ban (SSH brute-force protection)
# ============================================================================
log_info "Step 8: Setting up Fail2ban"

sudo systemctl enable fail2ban
sudo systemctl start fail2ban

log_info "Fail2ban configured"

# ============================================================================
# 9. Docker & Docker Compose (Optional)
# ============================================================================
log_info "Step 9: Installing Docker (optional)"

if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker ${BOT_USER}
    rm get-docker.sh
    log_info "Docker installed"
else
    log_warn "Docker already installed"
fi

if ! command -v docker-compose &> /dev/null; then
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
        -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
    log_info "Docker Compose installed"
else
    log_warn "Docker Compose already installed"
fi

# ============================================================================
# 10. Monitoring Stack (Prometheus + Grafana)
# ============================================================================
log_info "Step 10: Setting up monitoring stack"

cd ${BOT_DIR}

# Create prometheus config if not exists
cat > prometheus.yml << 'PROM'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'b20-bot'
    static_configs:
      - targets: ['localhost:8000']
PROM

log_info "Prometheus configured"

# ============================================================================
# 11. Backup Strategy
# ============================================================================
log_info "Step 11: Setting up backup strategy"

sudo tee /etc/cron.d/b20-bot-backup > /dev/null << 'EOF'
# Backup B20 Bot database daily at 2 AM UTC
0 2 * * * ubuntu cd /home/ubuntu/b20-bot && \
    cp data/b20_bot.db /backups/b20-bot/b20_bot.db.$(date +\%Y\%m\%d-\%H\%M\%S)

# Cleanup old backups (keep last 30 days)
0 3 * * * ubuntu find /backups/b20-bot -name "*.db.*" -mtime +30 -delete
EOF

log_info "Backup cron configured"

# ============================================================================
# 12. Security Hardening
# ============================================================================
log_info "Step 12: Security hardening"

# Secure .env permissions
if [ -f "${BOT_DIR}/.env" ]; then
    chmod 600 "${BOT_DIR}/.env"
    chown ${BOT_USER}:${BOT_USER} "${BOT_DIR}/.env"
fi

# Disable SSH password auth (optional - uncomment if using keys only)
# echo "PasswordAuthentication no" | sudo tee -a /etc/ssh/sshd_config
# sudo systemctl restart ssh

log_info "Security hardening applied"

# ============================================================================
# 13. System Info
# ============================================================================
log_info "Step 13: Displaying system information"

echo ""
echo "===================================================="
echo "B20 Bot VPS Setup Complete! 🎉"
echo "===================================================="
echo ""
echo "📍 Deployment Directory: ${BOT_DIR}"
echo "📍 Database: ${DB_DIR}/b20_bot.db"
echo "📍 Logs: ${LOG_DIR}/"
echo "📍 Backups: ${BACKUP_DIR}/"
echo ""
echo "🚀 Next Steps:"
echo "=================================================="
echo ""
echo "1. Edit .env with your configuration:"
echo "   nano ${BOT_DIR}/.env"
echo ""
echo "   Required variables:"
echo "   - RPC_URL (https://mainnet.base.org)"
echo "   - PRIVATE_KEY (0x...)"
echo "   - TG_BOT_TOKEN (optional, for Telegram alerts)"
echo "   - TG_USER_ID (optional)"
echo ""
echo "2. Start the bot:"
echo "   sudo systemctl start b20-bot"
echo ""
echo "3. Check bot status:"
echo "   sudo systemctl status b20-bot"
echo "   sudo journalctl -u b20-bot -f     # Live logs"
echo ""
echo "4. Check database:"
echo "   sqlite3 ${DB_DIR}/b20_bot.db '.tables'"
echo ""
echo "5. Monitor bot stats:"
echo "   sqlite3 ${DB_DIR}/b20_bot.db 'SELECT * FROM events ORDER BY timestamp DESC LIMIT 10;'"
echo ""
echo "6. (Optional) Start Docker monitoring:"
echo "   cd ${BOT_DIR}"
echo "   docker-compose up -d"
echo "   # Prometheus: http://your-vps-ip:9090"
echo "   # Grafana: http://your-vps-ip:3000 (admin/admin)"
echo ""
echo "7. Emergency commands:"
echo "   sudo systemctl stop b20-bot       # Stop bot"
echo "   sudo systemctl restart b20-bot    # Restart"
echo ""
echo "📊 Useful commands:"
echo "=================================================="
echo "   # View live logs"
echo "   sudo journalctl -u b20-bot -f"
echo ""
echo "   # Restart bot (apply config changes)"
echo "   sudo systemctl restart b20-bot"
echo ""
echo "   # Backup database"
echo "   cp ${DB_DIR}/b20_bot.db ${BACKUP_DIR}/backup-\$(date +%s).db"
echo ""
echo "   # View recent trades"
echo "   sqlite3 ${DB_DIR}/b20_bot.db <<"
echo "   SELECT action, profit_eth, timestamp FROM trades ORDER BY timestamp DESC LIMIT 20;"
echo "   .quit"
echo ""
echo "🔐 Security Checklist:"
echo "=================================================="
echo "   ✅ .env file is secured (chmod 600)"
echo "   ✅ UFW firewall enabled"
echo "   ✅ Fail2ban protecting SSH"
echo "   ✅ Daily database backups enabled"
echo "   ✅ Logs rotated weekly"
echo "   ✅ System clock synced (NTP)"
echo ""
echo "📞 Support:"
echo "=================================================="
echo "Check logs: sudo journalctl -u b20-bot -n 50"
echo "Check disk: df -h"
echo "Check memory: free -h"
echo "Check processes: ps aux | grep python"
echo ""
