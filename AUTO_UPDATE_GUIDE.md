# 🔄 AUTO-UPDATE & AUTO-RUN GUIDE

Your B20 bot can now **automatically update and restart** whenever you push code changes.

---

## 🚀 QUICK START (5 minutes)

### Option 1: Auto-Update Every Hour (EASIEST)

**Step 1:** Copy auto-update script to VPS
```bash
# From your local machine:
scp -i b20.pem auto-update.sh ubuntu@18.153.96.155:/home/ubuntu/b20-bot/
```

**Step 2:** Make it executable
```bash
ssh -i b20.pem ubuntu@18.153.96.155
chmod +x /home/ubuntu/b20-bot/auto-update.sh
```

**Step 3:** Add to crontab (checks every hour)
```bash
crontab -e

# Add this line at the bottom:
0 * * * * /home/ubuntu/b20-bot/auto-update.sh >> /home/ubuntu/b20-bot/logs/cron.log 2>&1
```

✅ **Done!** Bot will auto-update every hour

---

## 🎯 WHAT AUTO-UPDATE DOES

When triggered (hourly or on-demand):

1. **Checks for code changes**
   ```
   ✅ Fetches latest from git
   ✅ Compares with current version
   ```

2. **If changes found:**
   ```
   ✅ Pulls code
   ✅ Updates dependencies (pip install)
   ✅ Restarts bot service
   ✅ Verifies bot is running
   ```

3. **Logs everything**
   ```
   ✅ Records all actions
   ✅ Timestamps each step
   ✅ Alerts on failures
   ```

---

## 🔧 SETUP OPTIONS

### Option A: Hourly Cron Job (RECOMMENDED)
```bash
# Runs every hour automatically
crontab -e
0 * * * * /home/ubuntu/b20-bot/auto-update.sh
```
✅ **Pros:** Simple, no dependencies  
❌ **Cons:** Waits up to 1 hour

### Option B: Manual On-Demand
```bash
# Run whenever you want
/home/ubuntu/b20-bot/auto-update.sh

# Or from anywhere:
ssh -i b20.pem ubuntu@18.153.96.155 /home/ubuntu/b20-bot/auto-update.sh
```
✅ **Pros:** Instant updates  
❌ **Cons:** Manual trigger

### Option C: GitHub Webhook (ADVANCED)
Auto-triggers when you push to GitHub:

```bash
# 1. Create webhook receiver script
cat > /home/ubuntu/b20-bot/webhook-receiver.sh << 'EOF'
#!/bin/bash
# Runs when GitHub sends webhook
/home/ubuntu/b20-bot/auto-update.sh
EOF
chmod +x /home/ubuntu/b20-bot/webhook-receiver.sh

# 2. Set up systemd service
sudo nano /etc/systemd/system/b20-webhook.service
```

Create with this content:
```ini
[Unit]
Description=B20 Webhook Receiver
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/ubuntu/b20-bot/webhook-server.py
Restart=always
User=ubuntu

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable b20-webhook
sudo systemctl start b20-webhook
```

---

## 📝 EXAMPLE: Complete Auto-Update Setup

### Step 1: Copy files to VPS
```bash
scp -i b20.pem auto-update.sh ubuntu@18.153.96.155:/home/ubuntu/b20-bot/
```

### Step 2: Test script manually first
```bash
ssh -i b20.pem ubuntu@18.153.96.155
chmod +x /home/ubuntu/b20-bot/auto-update.sh
/home/ubuntu/b20-bot/auto-update.sh
```

You should see:
```
[2025-01-15 14:23:45] ==========================================
[2025-01-15 14:23:45] 🔄 Starting auto-update check...
[2025-01-15 14:23:45] ==========================================
[2025-01-15 14:23:46] 📂 Working directory: /home/ubuntu/b20-bot
[2025-01-15 14:23:47] 📥 Pulling latest code...
[2025-01-15 14:23:48] ✅ No changes found - already up to date
```

### Step 3: Add to crontab
```bash
crontab -e

# Add to file:
0 * * * * /home/ubuntu/b20-bot/auto-update.sh >> /home/ubuntu/b20-bot/logs/cron.log 2>&1
```

### Step 4: Verify cron is set
```bash
crontab -l
# Should show the auto-update line
```

---

## 🧪 TESTING AUTO-UPDATE

### Test 1: Make a code change
```bash
# On your local machine:
git checkout nftboy07-implement-all-upgrades
echo "# test comment" >> monitor_service.py
git add .
git commit -m "Test auto-update"
git push origin nftboy07-implement-all-upgrades
```

### Test 2: Trigger update manually
```bash
ssh -i b20.pem ubuntu@18.153.96.155 /home/ubuntu/b20-bot/auto-update.sh
```

You should see:
```
[2025-01-15 14:30:00] 🔄 Starting auto-update check...
[2025-01-15 14:30:05] 🔄 Found 1 changed files - updating...
[2025-01-15 14:30:10] ✅ Code pulled successfully
[2025-01-15 14:30:15] 📦 Updating Python dependencies...
[2025-01-15 14:30:25] ✅ Dependencies updated
[2025-01-15 14:30:26] 🔄 Restarting b20-bot service...
[2025-01-15 14:30:28] ✅ Bot restarted successfully
[2025-01-15 14:30:31] ✅ Bot is running and healthy!
[2025-01-15 14:30:31] ✅ AUTO-UPDATE COMPLETED SUCCESSFULLY
```

### Test 3: Check cron ran automatically
```bash
tail -f /home/ubuntu/b20-bot/logs/auto-update.log
# Wait for next hour mark, should see update attempt
```

---

## 📊 CHECKING UPDATE STATUS

### View recent updates
```bash
tail -20 /home/ubuntu/b20-bot/logs/auto-update.log
```

### Check bot status
```bash
sudo systemctl status b20-bot
```

### Check latest git commit
```bash
cd /home/ubuntu/b20-bot && git log --oneline -5
```

### View update history
```bash
grep "AUTO-UPDATE" /home/ubuntu/b20-bot/logs/auto-update.log
```

---

## ⚠️ TROUBLESHOOTING

### Auto-update not triggering?

**Check if cron is running:**
```bash
sudo systemctl status cron
# Or on some systems:
sudo systemctl status crond
```

**Check cron logs:**
```bash
tail -20 /home/ubuntu/b20-bot/logs/cron.log
```

**Test cron manually:**
```bash
# This runs the command:
/home/ubuntu/b20-bot/auto-update.sh

# If it works, cron should work too
```

### Bot won't restart after update?

**Check logs:**
```bash
sudo journalctl -u b20-bot -n 50
```

**Manually restart:**
```bash
sudo systemctl restart b20-bot
sudo systemctl status b20-bot
```

**Check for errors:**
```bash
python3 -m py_compile /home/ubuntu/b20-bot/*.py
# If any errors, fix syntax
```

### Git pull failing?

**Check git config:**
```bash
cd /home/ubuntu/b20-bot
git remote -v
# Should show: origin https://github.com/nftboy07/B20.git

git status
# Should show branch tracking
```

**Force reset and retry:**
```bash
git fetch origin
git reset --hard origin/nftboy07-implement-all-upgrades
```

---

## 🔐 SECURITY NOTES

✅ **Auto-update uses:**
- SSH key authentication (no passwords)
- Read-only Git operations
- Automatic rollback on error
- Comprehensive logging
- Service restart validation

❌ **NOT included (intentionally):**
- Automatic database migrations
- Breaking change handling
- Manual approval step

---

## 🎯 WORKFLOW WITH AUTO-UPDATE

Now your workflow is:

1. **Make code changes locally**
   ```bash
   git checkout nftboy07-implement-all-upgrades
   vim telegram_bot_enhanced.py  # Make changes
   git add .
   git commit -m "Add feature X"
   git push origin nftboy07-implement-all-upgrades
   ```

2. **Auto-update on VPS** (next hour)
   ```
   🔄 Detects changes
   ✅ Pulls code
   ✅ Restarts bot
   ```

3. **Live immediately!**
   ```
   ✅ New feature runs on VPS
   ✅ Telegram bot updated
   ✅ New monitors active
   ```

---

## ⏰ SCHEDULING

Current cron schedule:
```
0 * * * *   ← Every hour at :00 minutes
```

To change frequency:

| Frequency | Crontab |
|-----------|---------|
| Every 15 min | `*/15 * * * *` |
| Every 30 min | `*/30 * * * *` |
| Every hour | `0 * * * *` |
| Every 6 hours | `0 */6 * * *` |
| Daily @ 2 AM | `0 2 * * *` |

Example - check every 15 minutes:
```bash
crontab -e
*/15 * * * * /home/ubuntu/b20-bot/auto-update.sh >> /home/ubuntu/b20-bot/logs/cron.log 2>&1
```

---

## 📞 SUPPORT

For issues:

```bash
# Check recent logs
tail -100 /home/ubuntu/b20-bot/logs/auto-update.log

# Check bot status
sudo systemctl status b20-bot

# View real-time logs
sudo journalctl -u b20-bot -f
```

**Ready to deploy?**

```bash
# 1. Copy script
scp -i b20.pem auto-update.sh ubuntu@18.153.96.155:/home/ubuntu/b20-bot/

# 2. Make executable and test
ssh -i b20.pem ubuntu@18.153.96.155 chmod +x /home/ubuntu/b20-bot/auto-update.sh

# 3. Add to crontab
ssh -i b20.pem ubuntu@18.153.96.155 << 'EOF'
(crontab -l 2>/dev/null; echo "0 * * * * /home/ubuntu/b20-bot/auto-update.sh >> /home/ubuntu/b20-bot/logs/cron.log 2>&1") | crontab -
EOF
```

Done! Your bot now auto-updates every hour. 🚀

