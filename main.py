"""
Newsstand Bot — Main Entry Point

Starts the Telegram bot with all handlers, initializes the database,
and (in webhook/Render mode) configures the keep-alive self-ping plus the
in-process scraper.

Both newspapers and magazines are link-shares — the bot sends the source
download link (e.g. Google Drive) rather than re-hosting PDFs, so there is no
Telegram storage channel.

Scraping: newspapers and magazines are both lightweight link-lookups, so the
always-on bot scrapes everything in-process on a short APScheduler interval.
This is the primary scraper; the GitHub Actions cron (run_scrapers.py) is only
a backup for when the bot host is down.
"""

import asyncio
import datetime
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram import BotCommand, Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes

from config import Config
from database.operations import sync_titles_from_config
from handlers import register_handlers
import os
import httpx

# ─── Logging ───────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            "bot.log", encoding="utf-8",
            maxBytes=5 * 1024 * 1024,  # 5 MB per file
            backupCount=3,             # keep 3 rotated backups
        ),
    ],
)

# Silence noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger("newsstand")


# ─── Bot Commands for BotFather Menu ───────────────────────────────────────

BOT_COMMANDS = [
    BotCommand("help", "Learn how to use me"),
    BotCommand("subscribe", "Subscribe to your favorite newspapers"),
    BotCommand("subscriptions", "View & manage your subscriptions"),
    BotCommand("get", "Get any newspaper from the archives"),
]


async def keep_alive_ping(url: str):
    """Periodically pings the webhook URL to prevent Render free-tier from sleeping.
    
    Creates a fresh httpx client for each ping to avoid stale connection pools.
    """
    await asyncio.sleep(60)  # Wait 1 minute to allow server to fully boot
    while True:
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
                response = await client.get(url)
                logger.info(f"Keep-alive ping to {url} returned status {response.status_code}")
        except Exception as e:
            logger.warning(f"Keep-alive ping to {url} failed: {e}")
        await asyncio.sleep(600)  # Ping every 10 minutes


# ─── In-process scraper ────────────────────────────────────────────────────

async def scheduled_scrape(application: Application) -> None:
    """Run a full scrape+deliver cycle (newspapers + magazines) in-process.

    Both kinds are now lightweight link-shares (HTTP fetch + link message, no
    PDF download/upload), so the always-on bot can scrape everything every few
    minutes. This makes the bot self-sufficient and removes the dependence on
    GitHub Actions' cron, which is delayed/skipped often enough that papers were
    going undelivered for hours. ``is_manual=False`` keeps the "newspapers only
    after 6am IST" window.
    """
    from run_scrapers import run_scrape_cycle
    try:
        await run_scrape_cycle(application.bot, is_manual=False)
    except Exception:
        logger.exception("Scheduled scrape failed")


# ─── Lifecycle Hooks ──────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    """Runs after Application.initialize() — set up DB and bot commands."""
    config = Config.get()
    
    # 1. Sync titles and packs from config.yaml to DB
    logger.info("Syncing titles from config...")
    await sync_titles_from_config(config.db_path, config.titles)

    # 2. Register bot commands in Telegram
    logger.info("Setting bot commands...")
    await application.bot.set_my_commands(BOT_COMMANDS)
    
    # 3. Store config in bot_data for handlers to access
    application.bot_data["config"] = config
    
    # 4. Start self-ping keep-alive if WEBHOOK_URL is configured
    # Store the task reference in bot_data to prevent garbage collection
    webhook_url = os.environ.get("WEBHOOK_URL")
    if webhook_url:
        logger.info(f"Starting keep-alive self-ping for {webhook_url}")
        task = asyncio.create_task(keep_alive_ping(webhook_url))
        application.bot_data["_keep_alive_task"] = task

    # 5. Start the in-process scraper (Render / webhook mode only). This is now
    #    the primary scraper for newspapers AND magazines; GitHub Actions is just
    #    a backup. Local polling dev runs rely on `python run_scrapers.py`.
    if webhook_url:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler

            interval = int(os.environ.get("SCRAPE_INTERVAL_MIN")
                           or os.environ.get("MAGAZINE_SCRAPE_INTERVAL_MIN")
                           or "15")
            scheduler = AsyncIOScheduler(timezone="UTC")
            scheduler.add_job(
                scheduled_scrape,
                trigger="interval",
                minutes=interval,
                args=[application],
                id="scrape",
                max_instances=1,   # never stack runs
                coalesce=True,      # collapse missed runs into one
                next_run_time=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=90),
            )
            scheduler.start()
            application.bot_data["_scheduler"] = scheduler
            logger.info("Started in-process scraper (newspapers + magazines, every %d min).", interval)
        except Exception:
            logger.exception("Failed to start in-process scheduler")

    logger.info("Bot initialized successfully!")


async def post_shutdown(application: Application) -> None:
    """Cleanup on shutdown."""
    logger.info("Shutting down...")

    # Stop the in-process scheduler if running
    scheduler = application.bot_data.get("_scheduler")
    if scheduler is not None:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass

    # Cancel keep-alive task if running
    keep_alive_task = application.bot_data.get("_keep_alive_task")
    if keep_alive_task and not keep_alive_task.done():
        keep_alive_task.cancel()
        try:
            await keep_alive_task
        except asyncio.CancelledError:
            pass

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

    config = Config.get()
    
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
        if not os.environ.get("WEBHOOK_SECRET"):
            logger.warning(
                "WEBHOOK_SECRET is not set — incoming webhook updates are not "
                "authenticated. Set WEBHOOK_SECRET on the host so only Telegram "
                "can post updates to this bot."
            )
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
