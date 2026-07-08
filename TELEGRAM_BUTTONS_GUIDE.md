# 🤖 Telegram Bot Button Commands Guide

## Overview
Your bot now has **full button-based interface** with inline keyboards for easy control.

---

## 📱 Button Menu Structure

### Main Menu (when you send `/start`)
```
[📊 Status]  [💰 Portfolio]
[📈 Positions] [📝 Trades]
[⏸ Pause]   [▶️ Resume]
[⚙️ Settings] [📊 Stats]
[❓ Help]    [🔄 Refresh]
```

---

## 🎮 Button Functions

### Navigation Buttons

| Button | Function | Shows |
|--------|----------|-------|
| **📊 Status** | Quick bot status | Active positions, daily loss, win rate |
| **💰 Portfolio** | Portfolio snapshot | Total PnL, recent trades, unrealized gains |
| **📈 Positions** | Open positions | List of all active trades with entry prices |
| **📝 Trades** | Trade history | Recent 10 trades with buy/sell prices |
| **📊 Stats** | Detailed analytics | Win rate, total PnL, trade count |
| **⚙️ Settings** | Configuration menu | Risk limits, max positions, daily loss limit |
| **❓ Help** | Help guide | Tutorial on using all features |
| **🔄 Refresh** | Update display | Re-fetch latest data |
| **◀️ Back** | Go back | Return to previous menu |

### Control Buttons

| Button | Function | Effect |
|--------|----------|--------|
| **⏸ Pause** | Pause trading | Bot stops making trades (still monitors) |
| **▶️ Resume** | Resume trading | Bot resumes trading again |

---

## 📋 Example Button Responses

### `/start` → Main Menu
```
🤖 B20 SNIPER BOT CONTROL PANEL

Select an option below to:
• Monitor positions & PnL
• Control trading
• View statistics
• Manage settings

⚡ Status: 🟢 ACTIVE
```

### Click `📊 Status`
```
📊 BOT STATUS

Active Positions: 2/5
Daily Loss: 0.0234 / 0.5000 ETH
Status: 🟢 RUNNING

Quick Stats:
• Win Rate: 68.5%
• Total PnL: +0.342 ETH
• Trades: 26

🕐 Updated: 14:23:45 UTC
```

### Click `💰 Portfolio`
```
💰 PORTFOLIO SNAPSHOT

Summary:
• Open Positions: 2
• Unrealized PnL: +0.085 ETH (+12.3%)
• Status: 🟢 Active

Recent Trades:
✅ BUY: +0.0450 ETH
✅ SELL: +0.0380 ETH
❌ BUY: -0.0150 ETH
✅ SELL: +0.0520 ETH
```

### Click `📈 Positions`
```
📈 OPEN POSITIONS (2)

Position 1:
• Token: 0x3f5c...
• Entry: 0.00000850 ETH
• Qty: 45000.0000
• Age: 45m
• SL: 0.00000680

Position 2:
• Token: 0x8f2a...
• Entry: 0.00001200 ETH
• Qty: 28500.0000
• Age: 12m
• SL: 0.00000960
```

### Click `⚙️ Settings`
```
⚙️ BOT SETTINGS

Risk Management:
• Max Position Size: 0.1000 ETH
• Max Positions: 5
• Daily Loss Limit: 0.5000 ETH
• Current Daily Loss: 0.0234 ETH

Blacklisted Tokens: 12

Bot Status: 🟢 RUNNING
```

---

## ⚡ Quick Actions from Alert Messages

When a new pool is detected, you get:

```
🚨 NEW POOL DETECTED!

Token: MEME69 (MEME)
Liquidity: 45.50 ETH
Safety Score: 92/100

[✅ Buy] [❌ Skip]
[📊 Check]
```

**Option:**
- **✅ Buy** - Execute buy immediately
- **❌ Skip** - Skip this pool
- **📊 Check** - View bot status

---

## 🔄 Auto-Updates

The bot automatically sends alerts:

```
🟢 BOT STARTED
• Monitoring pools: ✅
• Telegram connected: ✅
• Safety checks: ENABLED
• Auto-buy: ENABLED

Ready to snipe! 🚀
```

```
🆕 NEW POOL DETECTED
Token: BONK2 (BONK)
Liquidity: 25.3 ETH
Safety: 87/100
→ Executing buy... ⏳
```

```
📈 POSITION OPENED
Token: BONK2 (BONK)
Quantity: 125,000
Entry: 0.00002030 ETH
Stop Loss: 0.00001624 ETH
Take Profit 1: 0.00003245 ETH
```

```
💰 TAKE PROFIT HIT
Token: BONK2 (BONK)
Sold: 50,000 @ 0.00003245 ETH
Profit: +0.0620 ETH 🎉
```

---

## 🚀 How to Use

1. **Send `/start`** to bot
   - Main menu appears with buttons

2. **Click any button**
   - Data updates in real-time
   - Use ◀️ Back to navigate

3. **Control trading**
   - ⏸ Pause to stop
   - ▶️ Resume to continue

4. **Monitor performance**
   - Check 📊 Status regularly
   - View 📝 Trades for history
   - Review 📊 Stats for analytics

---

## 🔧 Customization

To add more buttons, edit `telegram_bot_enhanced.py`:

```python
keyboard = [
    [InlineKeyboardButton("📊 Status", callback_data="btn_status")],
    [InlineKeyboardButton("💰 Portfolio", callback_data="btn_portfolio")],
    # Add new button:
    [InlineKeyboardButton("🆕 New Feature", callback_data="btn_new_feature")],
]
```

Then add handler:
```python
elif action == "btn_new_feature":
    await self.show_new_feature(update, context)
```

---

## 📊 All Available Commands

| Command | Button | Response |
|---------|--------|----------|
| `/start` | All (◀️ Back) | Main menu |
| `btn_status` | 📊 | Bot status |
| `btn_portfolio` | 💰 | Portfolio view |
| `btn_positions` | 📈 | Open positions |
| `btn_trades` | 📝 | Trade history |
| `btn_stats` | 📊 | Statistics |
| `btn_settings` | ⚙️ | Settings |
| `btn_help` | ❓ | Help guide |
| `btn_pause` | ⏸ | Pause trading |
| `btn_resume` | ▶️ | Resume trading |
| `btn_start` | 🔄 | Refresh menu |

---

## 🎯 Tips

1. **Fastest commands**: Click buttons for instant response
2. **Real-time data**: ✅ All data is fetched live
3. **No typing**: Everything is buttons - super fast!
4. **Mobile friendly**: Works perfectly on phone
5. **Alert notifications**: Auto-sent when pools detected

---

## ⚠️ Troubleshooting

**Bot doesn't respond to buttons?**
- Check `TG_BOT_TOKEN` in `.env`
- Check `TG_USER_ID` in `.env`
- Verify bot is running: `sudo systemctl status b20-bot`

**No new pool alerts?**
- Check RPC connection: `RPC_URL` in `.env`
- Verify event monitoring is running
- Check logs: `sudo journalctl -u b20-bot -f`

**Button text is weird?**
- Update telegram-bot library: `pip install --upgrade python-telegram-bot`

---

## 📞 Support

For issues, check logs:
```bash
sudo journalctl -u b20-bot -f
tail -f /home/ubuntu/b20-bot/logs/monitor.log
```

