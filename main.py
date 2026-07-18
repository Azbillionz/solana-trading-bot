"""
Solana Trading Bot — Entry point.
Run: python main.py
"""
import asyncio
import os

from telegram import BotCommand
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters,
)

import config
from src.utils.logger import logger
from src.utils.database import init_db
from src.bot.handlers import (
    cmd_start, cmd_wallet, cmd_sniper, cmd_copytrade, cmd_positions,
    cmd_settings, cmd_referral, cmd_analyze, cmd_rugcheck,
    cmd_buy, cmd_sell, cmd_cancel, cmd_help, handle_message,
)
from src.bot.menus import handle_callback
from src.trading.limit_orders import run_limit_order_engine
from src.trading.dca import run_dca_engine
from src.trading.auto_sell import run_auto_sell_engine
from src.copy_trade.monitor import run_copy_trade_monitor


# ── Bot commands shown in Telegram's "/" menu ─────────────────────────────────

BOT_COMMANDS = [
    BotCommand("start",     "🏠 Main menu"),
    BotCommand("wallet",    "💼 Manage wallets"),
    BotCommand("buy",       "🟢 Quick buy a token"),
    BotCommand("sell",      "🔴 Quick sell a token"),
    BotCommand("positions", "📊 Open positions & orders"),
    BotCommand("sniper",    "🎯 Auto-sniper settings"),
    BotCommand("copytrade", "🔁 Copy trading"),
    BotCommand("settings",  "⚙️ Bot settings"),
    BotCommand("referral",  "🔗 Referral program"),
    BotCommand("rugcheck",  "🛡 Token safety check"),
    BotCommand("analyze",   "🔍 Wallet P&L analysis"),
    BotCommand("cancel",    "❌ Cancel active orders"),
    BotCommand("help",      "❓ Help & fee info"),
]


async def _notify_user(app: Application, user_id: int, msg: str) -> None:
    from src.bot.handlers import _notify_user as _n
    await _n(app, user_id, msg)


async def _start_background_tasks(app: Application) -> None:
    """Launch all background engine tasks after bot is ready."""
    logger.info("Starting background engines...")
    os.makedirs("logs", exist_ok=True)

    async def limit_notify(user_id, token, msg):
        await _notify_user(app, user_id, msg)

    async def dca_notify(user_id, token, msg):
        await _notify_user(app, user_id, msg)

    async def autosell_notify(user_id, token, msg):
        await _notify_user(app, user_id, msg)

    async def ct_notify(user_id, token, msg):
        await _notify_user(app, user_id, msg)

    asyncio.create_task(run_limit_order_engine(limit_notify))
    asyncio.create_task(run_dca_engine(dca_notify))
    asyncio.create_task(run_auto_sell_engine(autosell_notify))
    asyncio.create_task(run_copy_trade_monitor(ct_notify))
    logger.info("All background engines started.")


async def _post_init(app: Application) -> None:
    """Runs once after the Application is initialised — register commands."""
    await app.bot.set_my_commands(BOT_COMMANDS)
    logger.info("Bot commands registered with Telegram.")
    if config.ADMIN_TELEGRAM_IDS:
        logger.info("Admin IDs active: {}", config.ADMIN_TELEGRAM_IDS)


def build_application() -> Application:
    """Build and configure the Telegram Application."""
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("wallet",    cmd_wallet))
    app.add_handler(CommandHandler("sniper",    cmd_sniper))
    app.add_handler(CommandHandler("copytrade", cmd_copytrade))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("settings",  cmd_settings))
    app.add_handler(CommandHandler("referral",  cmd_referral))
    app.add_handler(CommandHandler("analyze",   cmd_analyze))
    app.add_handler(CommandHandler("rugcheck",  cmd_rugcheck))
    app.add_handler(CommandHandler("buy",       cmd_buy))
    app.add_handler(CommandHandler("sell",      cmd_sell))
    app.add_handler(CommandHandler("cancel",    cmd_cancel))
    app.add_handler(CommandHandler("help",      cmd_help))

    # Callback queries (inline keyboards)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Text message input (for conversation states)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    return app


async def main() -> None:
    os.makedirs("logs", exist_ok=True)
    logger.info("Initialising database...")
    await init_db()
    logger.info("Building Telegram application...")
    app = build_application()

    async with app:
        await _start_background_tasks(app)
        logger.info("Bot started. Polling for updates...")
        await app.updater.start_polling(
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )
        await app.start()
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Shutdown signal received.")
        finally:
            logger.info("Stopping bot...")
            await app.updater.stop()
            await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
