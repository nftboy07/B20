#!/bin/bash
# B20 Bot VPS Deployment Script
# Usage: bash deploy_to_vps.sh <vps_user@vps_ip> <key_path>

set -e

VPS_TARGET="${1:-ubuntu@YOUR_VPS_IP}"
KEY_PATH="${2:-~/.ssh/b20.pem}"
BOT_DIR="/home/ubuntu/b20-bot"
BACKUP_DIR="/home/ubuntu/b20-bot-backup-$(date +%s)"

echo "🚀 B20 Bot Deployment Script"
echo "=========================================="
echo "VPS Target: $VPS_TARGET"
echo "Key: $KEY_PATH"
echo ""

# Check key exists
if [ ! -f "$KEY_PATH" ]; then
    echo "❌ Key not found: $KEY_PATH"
    exit 1
fi

echo "📦 Step 1: Copy code to VPS..."
scp -i "$KEY_PATH" -r ./* "$VPS_TARGET:$BOT_DIR/" 2>/dev/null || {
    echo "Creating bot directory..."
    ssh -i "$KEY_PATH" "$VPS_TARGET" "mkdir -p $BOT_DIR"
    scp -i "$KEY_PATH" -r ./* "$VPS_TARGET:$BOT_DIR/"
}

echo "✅ Code copied"

echo ""
echo "🔧 Step 2: Setup VPS environment..."
ssh -i "$KEY_PATH" "$VPS_TARGET" << 'EOF'
    set -e
    
    cd /home/ubuntu/b20-bot
    
    echo "Updating system..."
    sudo apt-get update
    sudo apt-get install -y python3-venv python3-pip python3-dev git curl
    
    echo "Setting up Python venv..."
    python3 -m venv venv || true
    source venv/bin/activate
    
    echo "Installing dependencies..."
    pip install --upgrade pip
    pip install -r requirements.txt
    
    echo "Fixing permissions..."
    chmod 600 .env 2>/dev/null || true
    chmod +x start_bot.sh setup_vps.sh
    
    echo "Setting up systemd service..."
    sudo tee /etc/systemd/system/b20-bot.service > /dev/null << 'SYSTEMD'
[Unit]
Description=B20 Mainnet Sniper Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/b20-bot
Environment="PATH=/home/ubuntu/b20-bot/venv/bin"
ExecStart=/home/ubuntu/b20-bot/venv/bin/python b20_mainnet_sniper.py --monitor --live
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SYSTEMD
    
    sudo systemctl daemon-reload
    echo "Systemd service created"
EOF

echo "✅ VPS environment setup"

echo ""
echo "📊 Step 3: Docker setup (optional)..."
ssh -i "$KEY_PATH" "$VPS_TARGET" << 'EOF'
    set -e
    
    cd /home/ubuntu/b20-bot
    
    # Check if Docker installed
    if ! command -v docker &> /dev/null; then
        echo "Installing Docker..."
        curl -fsSL https://get.docker.com -o get-docker.sh
        sudo sh get-docker.sh
        sudo usermod -aG docker ubuntu
    fi
    
    # Check if Docker Compose installed
    if ! command -v docker-compose &> /dev/null; then
        echo "Installing Docker Compose..."
        sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
        sudo chmod +x /usr/local/bin/docker-compose
    fi
    
    echo "Docker setup complete"
EOF

echo "✅ Docker installed"

echo ""
echo "✨ Step 4: Database initialization..."
ssh -i "$KEY_PATH" "$VPS_TARGET" << 'EOF'
    cd /home/ubuntu/b20-bot
    source venv/bin/activate
    python << 'PYEOF'
from db_manager import DBManager
db = DBManager("/home/ubuntu/b20-bot/data/b20_bot.db")
print("✅ Database initialized")
PYEOF
EOF

echo "✅ Database ready"

echo ""
echo "🔐 Step 5: Security hardening..."
ssh -i "$KEY_PATH" "$VPS_TARGET" << 'EOF'
    # Secure .env
    chmod 600 /home/ubuntu/b20-bot/.env
    
    # Create log directory
    mkdir -p /home/ubuntu/b20-bot/logs
    chmod 700 /home/ubuntu/b20-bot/logs
    
    # Disable SSH password auth (optional)
    # echo "PasswordAuthentication no" | sudo tee -a /etc/ssh/sshd_config
    # sudo systemctl restart ssh
    
    echo "Security hardened"
EOF

echo "✅ Security hardened"

echo ""
echo "📝 Step 6: Create startup script..."
ssh -i "$KEY_PATH" "$VPS_TARGET" << 'EOF'
    cat > /home/ubuntu/b20-bot/start_production.sh << 'BASHEOF'
#!/bin/bash

# B20 Bot Production Start Script
set -e

cd /home/ubuntu/b20-bot

echo "🚀 Starting B20 Bot..."

# Option 1: Systemd (recommended)
echo "Starting via systemd..."
sudo systemctl start b20-bot
sudo systemctl enable b20-bot

echo "Bot started! Check status with:"
echo "  sudo systemctl status b20-bot"
echo ""
echo "View logs:"
echo "  sudo journalctl -u b20-bot -f"
BASHEOF

    chmod +x /home/ubuntu/b20-bot/start_production.sh
    echo "Startup script created"
EOF

echo "✅ Startup script ready"

echo ""
echo "🎉 Deployment Complete!"
echo "=========================================="
echo ""
echo "Next steps on VPS:"
echo ""
echo "1. Edit .env with real settings:"
echo "   nano /home/ubuntu/b20-bot/.env"
echo ""
echo "2. Start the bot:"
echo "   bash /home/ubuntu/b20-bot/start_production.sh"
echo ""
echo "3. Monitor logs:"
echo "   sudo journalctl -u b20-bot -f"
echo ""
echo "4. Check database:"
echo "   sqlite3 /home/ubuntu/b20-bot/data/b20_bot.db .tables"
echo ""
echo "Alternative: Docker Compose"
echo "   cd /home/ubuntu/b20-bot"
echo "   docker-compose up -d"
echo "   docker-compose logs -f"
echo ""
