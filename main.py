"""
Newsstand Bot — Main Entry Point

Starts the Telegram bot with all handlers, initializes the database,
sets up the scraper manager, and starts the delivery scheduler.
"""

import asyncio
import logging
import sys
from pathlib import Path

from telegram import BotCommand, Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes

from config import Config
from database.operations import sync_titles_from_config, sync_packs_from_config
from handlers import register_handlers
import os

# ─── Logging ───────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)

# Silence noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

logger = logging.getLogger("newsstand")


# ─── Bot Commands for BotFather Menu ───────────────────────────────────────

BOT_COMMANDS = [
    BotCommand("help", "Learn how to use me"),
    BotCommand("subscribe", "Subscribe to your favorite newspapers"),
    BotCommand("unsubscribe", "Unsubscribe from newspapers"),
    BotCommand("subscriptions", "View your active subscriptions"),
    BotCommand("get", "Get any newspaper from the archives"),
    BotCommand("today", "Get today's newspapers instantly"),
]


# ─── Lifecycle Hooks ──────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    """Runs after Application.initialize() — set up DB, scrapers, scheduler."""
    config = Config()
    
    # 1. Sync titles and packs from config.yaml to DB
    logger.info("Syncing titles from config...")
    await sync_titles_from_config(config.db_path, config.titles)
    
    logger.info("Syncing packs from config...")
    await sync_packs_from_config(config.db_path, config.packs)
    
    # 5. Register bot commands in Telegram
    logger.info("Setting bot commands...")
    await application.bot.set_my_commands(BOT_COMMANDS)
    
    # 6. Store config in bot_data for handlers to access
    application.bot_data["config"] = config
    
    logger.info("Bot initialized successfully!")


async def post_shutdown(application: Application) -> None:
    """Cleanup on shutdown."""
    logger.info("Shutting down...")
    logger.info("Bot shut down cleanly.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler."""
    logger.error(f"Exception while handling update: {context.error}", exc_info=context.error)
    
    # Try to notify the user
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ Something went wrong. Please try again later.\n"
                "If this keeps happening, contact the admin."
            )
        except Exception:
            pass


# ─── Main ─────────────────────────────────────────────────────────────────

def main() -> None:
    """Build and run the bot."""
    # Ensure there is an event loop (fixes PTB on Python 3.10+)
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    config = Config()
    
    if not config.bot_token:
        logger.error("BOT_TOKEN not set! Copy .env.example to .env and fill in your token.")
        sys.exit(1)
    
    logger.info("Building application...")
    
    # Ensure data directories exist
    Path(config.download_dir).mkdir(parents=True, exist_ok=True)
    
    # Build the application
    app = (
        ApplicationBuilder()
        .token(config.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(True)
        .build()
    )
    
    # Register handlers
    register_handlers(app)
    
    # Register error handler
    app.add_error_handler(error_handler)
    
    # Run with polling or webhook based on ENV
    webhook_url = os.environ.get("WEBHOOK_URL")
    
    if webhook_url:
        port = int(os.environ.get("PORT", 8080))
        logger.info(f"Starting bot with Webhook on port {port} -> {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            webhook_url=webhook_url,
            secret_token=os.environ.get("WEBHOOK_SECRET"),
            drop_pending_updates=True,
        )
    else:
        logger.info("Starting bot with polling (Local Dev Mode)...")
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()
