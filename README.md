# B20 Mainnet Sniper / Launcher Bot

**Base Mainnet (chainId 8453) ONLY.**

This bot is built strictly following the provided Mainnet-specific instructions for the B20 (Beryl upgrade) activation.

## Key Addresses (Mainnet)
- Activation Registry: `0x8453000000000000000000000000000000000001`
- Policy Registry: `0x8453000000000000000000000000000000000002`
- B20Factory: `0xB20f000000000000000000000000000000000000`
- Uniswap V3 Factory: `0x33128a8fC17869897dcE68Ed026d694621f6FDfD`
- Uniswap V3 Router: `0xE592427A0AEce92De3Edee1F18E0157C05861564`
- WETH: `0x4200000000000000000000000000000000000006`

Activation scheduled: **July 8, 2026 18:00 UTC**.

## Setup
```bash
python -m venv venv
source venv/bin/activate   # or venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env
# edit .env with your Mainnet RPC + PRIVATE_KEY  (NEVER commit .env)
```

## Run
```powershell
# Dry-run / simulation (safe, recommended)
python b20_mainnet_sniper.py --monitor

# With live transactions (REAL MONEY - EXTREME CAUTION)
python b20_mainnet_sniper.py --live --monitor --buy-amount 0.02

# Attempt createB20 (only after activation + with --live)
python b20_mainnet_sniper.py --live --create-b20 --salt "your-unique-salt"
```

## Features Implemented
- `mainnet_sanity_check` (exact spec)
- `isActivated` (Activation Registry) before createB20
- Gas via `eth_feeHistory` + 50-100%+ premium
- Uniswap V3 `PoolCreated` monitoring (all fee tiers: 500/3000/10000)
- Liquidity check via `pool.liquidity()` before buy
- Retry logic on failure (higher gas, lower effective slippage)
- Simulation via `eth_call` at 'pending' before sends
- B20 address detection (0xB20... + `isB20()` call)
- Flashbots RPC support via env (set as RPC_URL or FLASHBOTS_RPC)
- Hardcoded chainId=8453 everywhere

## Critical Safety Notes
- Always start in dry-run.
- Use `eth_call` simulation for any `createB20` or buy calldata.
- Never run createB20 before the registry returns true for the feature.
- Real ETH required. Failed tx still cost gas.
- Set realistic `amountOutMinimum` in production (current demo uses 0 for illustration — very dangerous).
- For accurate `params` / `initCalls` encoding for `createB20`, use the encoding helpers from the official `base-std` library (Solidity) or replicate exactly. The Python encoder here is illustrative only.
- Sync your system clock to UTC/NTP.
- Have a backup RPC (Infura, Alchemy, etc.).

## Recommended Flow for Launch
1. Run monitor in dry-run days/hours before.
2. At T-5min before 18:00 UTC confirm `isActivated` via the bot or cast.
3. Switch to live only when ready and funded.
4. For private submission, configure a suitable private / builder RPC.

Use at your own risk. Gas is real. Slippages on launch pools are brutal.

## VPS Deployment (using b20.pem)
1. On your local machine (with the PEM):
   ```powershell
   # Replace USER and IP with your Lightsail/AWS instance details (common users: ubuntu, root, admin)
   $KEY = "C:\Users\91907\Downloads\b20.pem"
   $VPS = "ubuntu@YOUR_VPS_IP_HERE"

   # Fix key perms if needed (run once)
   icacls $KEY /inheritance:r /grant:r "$env:USERNAME:(R)"

   # Copy the project
   scp -i $KEY -r C:\Users\91907\B20-repo $VPS:/home/ubuntu/b20-bot

   # SSH in
   ssh -i $KEY $VPS
   ```

2. On the VPS:
   ```bash
   cd /home/ubuntu/b20-bot   # adjust path
   sudo apt update && sudo apt install -y python3-venv python3-pip git
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt

   # Create .env with REAL values (use editor or scp a prepared .env)
   cp .env.example .env
   nano .env   # set RPC_URL (Alchemy/Infura recommended), PRIVATE_KEY, etc.
   ```

3. Run persistently:
   ```bash
   # Using tmux (recommended)
   tmux new -s b20
   source venv/bin/activate
   python b20_mainnet_sniper.py --monitor     # or with --live when ready

   # Detach: Ctrl+B then D
   # Reattach later: tmux attach -t b20
   ```

   Or use nohup / systemd for production.

**Security:** Never put your main wallet private key on a long-lived VPS without additional protections. Consider a dedicated sniper wallet with limited funds. Use a private RPC. Monitor logs.

## Telegram Integration
Add to `.env`:
```
TG_BOT_TOKEN=your_bot_token_from_BotFather
TG_USER_ID=your_user_id_from_userinfobot
```

The bot will send notifications for:
- Startup
- New pools (with B20 flag)
- Buy attempts and results

Restart after editing: `sudo systemctl restart b20-bot`

