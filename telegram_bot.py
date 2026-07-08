#!/usr/bin/env python3
"""
Enhanced Telegram Bot Module
=============================
Full interactive Telegram bot for B20 Bot control:
- /status - view positions, PnL, stats
- /pause / /resume - control monitoring
- /buy <token> <amount> - manual buys
- /sell <token> <percent> - manual sells
- /blacklist <token> - blacklist token
- /positions - list open positions
- /portfolio - portfolio snapshot
- /help - command help
- Real-time alerts with action buttons
"""

import json
import logging
from typing import Dict, Optional, List, Tuple
from datetime import datetime, timezone

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
    from telegram.ext import (
        Application, CommandHandler, CallbackQueryHandler,
        ContextTypes, MessageHandler, filters
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TelegramBotManager:
    """Manage Telegram bot commands and callbacks."""

    def __init__(self, bot_token: str, user_id: int):
        self.bot_token = bot_token
        self.user_id = user_id
        self.app = None
        
        # State references (passed in)
        self.db_manager = None
        self.risk_manager = None
        self.w3 = None

    def initialize(self, db_manager, risk_manager, w3):
        """Initialize with bot dependencies."""
        self.db_manager = db_manager
        self.risk_manager = risk_manager
        self.w3 = w3
        
        if TELEGRAM_AVAILABLE:
            self.app = Application.builder().token(self.bot_token).build()
            self._setup_handlers()

    def _setup_handlers(self):
        """Register command and callback handlers."""
        if not self.app:
            return
        
        # Commands
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("positions", self.cmd_positions))
        self.app.add_handler(CommandHandler("portfolio", self.cmd_portfolio))
        self.app.add_handler(CommandHandler("pause", self.cmd_pause))
        self.app.add_handler(CommandHandler("resume", self.cmd_resume))
        self.app.add_handler(CommandHandler("buy", self.cmd_buy))
        self.app.add_handler(CommandHandler("sell", self.cmd_sell))
        self.app.add_handler(CommandHandler("blacklist", self.cmd_blacklist))
        self.app.add_handler(CommandHandler("stats", self.cmd_stats))
        
        # Callbacks for buttons
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        if update.effective_user.id != self.user_id:
            await update.message.reply_text("Unauthorized")
            return
        
        keyboard = [
            [InlineKeyboardButton("📊 Status", callback_data="status")],
            [InlineKeyboardButton("📈 Portfolio", callback_data="portfolio")],
            [InlineKeyboardButton("⏸ Pause", callback_data="pause"),
             InlineKeyboardButton("▶ Resume", callback_data="resume")],
            [InlineKeyboardButton("💾 Positions", callback_data="positions")],
            [InlineKeyboardButton("🆘 Help", callback_data="help")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🚀 **B20 Bot Control Panel**\n\nSelect an option:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        if update.effective_user.id != self.user_id:
            await update.message.reply_text("Unauthorized")
            return
        
        if not self.risk_manager:
            await update.message.reply_text("Bot not initialized")
            return
        
        stats = self.risk_manager.get_stats()
        db_stats = self.db_manager.get_stats() if self.db_manager else {}
        
        message = f"""
**📊 Bot Status**

**Risk Manager:**
• Open Positions: {stats['open_positions']}/{stats['max_positions']}
• Daily Loss: {stats['daily_loss_eth']:.4f} / {stats['daily_loss_limit_eth']:.4f} ETH
• Consecutive Losses: {stats['consecutive_losses']}
• Paused: {'Yes ⏸' if stats['is_paused'] else 'No ▶'}
• Blacklisted: {stats['blacklisted_count']} tokens

**Database Stats:**
• Total Trades: {db_stats.get('total_trades', 0)}
• Successful: {db_stats.get('successful_trades', 0)}
• Win Rate: {db_stats.get('win_rate', 0):.1f}%
• Total PnL: {db_stats.get('total_pnl_eth', 0):.4f} ETH

**Time:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
"""
        
        await update.message.reply_text(message, parse_mode="Markdown")

    async def cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /positions command."""
        if update.effective_user.id != self.user_id:
            await update.message.reply_text("Unauthorized")
            return
        
        if not self.risk_manager:
            await update.message.reply_text("Bot not initialized")
            return
        
        positions = self.risk_manager.get_positions()
        
        if not positions:
            await update.message.reply_text("No open positions")
            return
        
        message = "**📈 Open Positions**\n\n"
        for i, pos in enumerate(positions, 1):
            message += f"""
**Position {i}:**
• Token: `{pos.token_address[:10]}...`
• Entry: {pos.entry_price:.8f} ETH
• Quantity: {pos.quantity:.4f}
• Age: {(datetime.now(timezone.utc) - pos.entry_time).total_seconds()/60:.1f}m
• Stop Loss: {pos.stop_loss_price:.8f}
"""
        
        await update.message.reply_text(message, parse_mode="Markdown")

    async def cmd_portfolio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /portfolio command."""
        if update.effective_user.id != self.user_id:
            await update.message.reply_text("Unauthorized")
            return
        
        if not self.risk_manager:
            await update.message.reply_text("Bot not initialized")
            return
        
        # Get current prices (simplified - would need real data)
        prices = {}
        pnl_eth, pnl_percent = self.risk_manager.calculate_portfolio_pnl(prices)
        
        message = f"""
**💰 Portfolio Snapshot**

• Open Positions: {self.risk_manager.get_position_count()}
• Unrealized PnL: {pnl_eth:+.4f} ETH ({pnl_percent:+.1f}%)
• Daily Loss: {self.risk_manager.daily_loss_eth:.4f} ETH
• Status: {'🔴 Paused' if self.risk_manager.is_paused else '🟢 Active'}

**Recent Trades:**
"""
        
        if self.db_manager:
            trades = self.db_manager.get_trades(limit=5)
            for trade in trades:
                status_icon = "✅" if trade['status'] == 'success' else "❌"
                message += f"\n{status_icon} {trade['action'].upper()}: {trade['profit_eth']:+.4f} ETH"
        
        await update.message.reply_text(message, parse_mode="Markdown")

    async def cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /pause command."""
        if update.effective_user.id != self.user_id:
            await update.message.reply_text("Unauthorized")
            return
        
        if self.risk_manager:
            self.risk_manager.pause()
            await update.message.reply_text("⏸ Bot paused - no new trades")
            
            if self.db_manager:
                self.db_manager.log_event("bot_paused", "warning", "User paused bot")

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /resume command."""
        if update.effective_user.id != self.user_id:
            await update.message.reply_text("Unauthorized")
            return
        
        if self.risk_manager:
            self.risk_manager.resume()
            await update.message.reply_text("▶ Bot resumed")
            
            if self.db_manager:
                self.db_manager.log_event("bot_resumed", "info", "User resumed bot")

    async def cmd_buy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /buy <token> <amount> command."""
        if update.effective_user.id != self.user_id:
            await update.message.reply_text("Unauthorized")
            return
        
        if not context.args or len(context.args) < 2:
            await update.message.reply_text("Usage: /buy <token_address> <amount_eth>")
            return
        
        token_address = context.args[0]
        try:
            amount_eth = float(context.args[1])
        except ValueError:
            await update.message.reply_text("Invalid amount")
            return
        
        await update.message.reply_text(
            f"Manual buy pending:\n• Token: `{token_address}`\n• Amount: {amount_eth} ETH\n\n(Manual execution not implemented)",
            parse_mode="Markdown"
        )
        
        if self.db_manager:
            self.db_manager.log_event(
                "manual_buy_request", "info",
                f"Manual buy request for {token_address}",
                {"amount_eth": amount_eth}
            )

    async def cmd_sell(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /sell <token> <percent> command."""
        if update.effective_user.id != self.user_id:
            await update.message.reply_text("Unauthorized")
            return
        
        if not context.args or len(context.args) < 2:
            await update.message.reply_text("Usage: /sell <token_address> <percent>")
            return
        
        token_address = context.args[0]
        try:
            percent = float(context.args[1])
        except ValueError:
            await update.message.reply_text("Invalid percent")
            return
        
        await update.message.reply_text(
            f"Manual sell pending:\n• Token: `{token_address}`\n• Percent: {percent}%\n\n(Manual execution not implemented)",
            parse_mode="Markdown"
        )
        
        if self.db_manager:
            self.db_manager.log_event(
                "manual_sell_request", "info",
                f"Manual sell request for {token_address}",
                {"percent": percent}
            )

    async def cmd_blacklist(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /blacklist <token> command."""
        if update.effective_user.id != self.user_id:
            await update.message.reply_text("Unauthorized")
            return
        
        if not context.args:
            await update.message.reply_text("Usage: /blacklist <token_address>")
            return
        
        token_address = context.args[0]
        
        if self.risk_manager:
            self.risk_manager.blacklist_token(token_address)
            await update.message.reply_text(f"✅ Blacklisted: `{token_address}`", parse_mode="Markdown")

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command."""
        if update.effective_user.id != self.user_id:
            await update.message.reply_text("Unauthorized")
            return
        
        if not self.db_manager:
            await update.message.reply_text("Database not initialized")
            return
        
        stats = self.db_manager.get_stats()
        
        message = f"""
**📈 Trading Stats**

• Total Trades: {stats['total_trades']}
• Successful: {stats['successful_trades']}
• Win Rate: {stats['win_rate']:.1f}%
• Total PnL: {stats['total_pnl_eth']:+.4f} ETH
• Open Positions: {stats['open_positions']}
"""
        
        await update.message.reply_text(message, parse_mode="Markdown")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        if update.effective_user.id != self.user_id:
            await update.message.reply_text("Unauthorized")
            return
        
        message = """
**B20 Bot Commands:**

📊 *Status & Info*
• `/status` - Show bot status
• `/stats` - Trading statistics
• `/positions` - List open positions
• `/portfolio` - Portfolio snapshot

🎮 *Control*
• `/pause` - Pause trading
• `/resume` - Resume trading
• `/help` - Show this help

🔧 *Manual Trading*
• `/buy <token> <amount>` - Manual buy
• `/sell <token> <percent>` - Manual sell
• `/blacklist <token>` - Blacklist token

**Example:**
`/buy 0xb20000... 0.05`
`/sell 0xb20000... 50`
"""
        
        await update.message.reply_text(message, parse_mode="Markdown")

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button callbacks."""
        if update.effective_user.id != self.user_id:
            await update.callback_query.answer("Unauthorized", show_alert=True)
            return
        
        data = update.callback_query.data
        
        if data == "status":
            await self.cmd_status(update, context)
        elif data == "portfolio":
            await self.cmd_portfolio(update, context)
        elif data == "positions":
            await self.cmd_positions(update, context)
        elif data == "pause":
            await self.cmd_pause(update, context)
        elif data == "resume":
            await self.cmd_resume(update, context)
        elif data == "help":
            await self.cmd_help(update, context)
        
        await update.callback_query.answer()

    async def start_polling(self):
        """Start the bot in polling mode."""
        if not self.app:
            logger.warning("Telegram bot not available (python-telegram-bot not installed)")
            return
        
        logger.info("Starting Telegram bot polling...")
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()

    async def stop(self):
        """Stop the bot."""
        if self.app:
            await self.app.stop()

    async def send_alert(self, title: str, message: str, buttons: List[Tuple[str, str]] = None):
        """Send alert with optional action buttons."""
        if not TELEGRAM_AVAILABLE or not self.app:
            logger.warning("Telegram not available for alert")
            return
        
        text = f"**{title}**\n\n{message}"
        
        keyboard = None
        if buttons:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(label, callback_data=data)]
                for label, data in buttons
            ])
        
        try:
            await self.app.bot.send_message(
                chat_id=self.user_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")

    async def send_notification(self, message: str):
        """Send simple notification."""
        if not TELEGRAM_AVAILABLE or not self.app:
            logger.warning("Telegram not available for notification")
            return
        
        try:
            await self.app.bot.send_message(
                chat_id=self.user_id,
                text=message,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
