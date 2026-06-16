"""
Newsstand Bot — /start and /help handlers
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from database.operations import register_user

logger = logging.getLogger(__name__)


# ── /start ───────────────────────────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Register user and send a beautiful welcome message."""
    user = update.effective_user
    if not user:
        return

    try:
        await register_user(
            db_path=context.bot_data["config"].db_path,
            user_id=user.id,
            username=user.username or "",
            first_name=user.first_name or "",
        )
    except Exception:
        logger.exception("Failed to register user %s", user.id)

    welcome = (
        "🗞️ <b>Welcome to Newsstand Bot!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Hey <b>{user.first_name}</b>! 👋\n\n"
        "I deliver your favourite newspapers &amp; magazines "
        "straight to Telegram — every single morning. ☕📰\n\n"
        "📌 <b>Quick Start</b>\n"
        "┌─────────────────────────────\n"
        "│ 1️⃣  /subscribe — Pick your papers\n"
        "│ 2️⃣  /get — Browse the archives\n"
        "└─────────────────────────────\n\n"
        "📖 <b>All Commands</b>\n\n"
        "  <b>Browsing</b>\n"
        "  /subscribe — Interactive title browser\n\n"
        "  <b>Subscriptions</b>\n"
        "  /sub &lt;title&gt; — Quick subscribe\n"
        "  /unsub &lt;title&gt; or /unsubscribe — Quick unsubscribe\n"
        "  /subscriptions — View active subs\n\n"
        "  <b>Reading</b>\n"
        "  /get — Interactive archive browser\n\n"
        "  /help — Full command reference\n\n"
        "Let's get you set up! Tap /subscribe to begin 🚀"
    )

    await update.message.reply_text(welcome, parse_mode="HTML")


# ── /help ────────────────────────────────────────────────────────────────────

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send detailed command reference grouped by category."""
    text = (
        "📚 <b>Newsstand Bot — Command Reference</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "🔎 <b>Browsing &amp; Discovery</b>\n"
        "  /subscribe — Interactive language → title browser\n\n"

        "📋 <b>Subscriptions</b>\n"
        "  /sub &lt;title&gt; — Quick-subscribe by name\n"
        "  /unsub &lt;title&gt; or /unsubscribe — Quick-unsubscribe by name\n"
        "  /subscriptions — View &amp; manage your active subs\n\n"

        "📰 <b>Reading &amp; Retrieval</b>\n"
        "  /get — Interactive archive browser (select title and date)\n\n"

        "💡 <i>Tip: You can use partial names with /sub — "
        "the bot will fuzzy-match them for you!</i>"
    )

    await update.message.reply_text(text, parse_mode="HTML")


# ── /run_scraper (Admin only) ────────────────────────────────────────────────

async def run_scraper_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger the scraper manually (admin only)."""
    user_id = update.effective_user.id
    config = context.bot_data.get("config")
    
    # Fallback to loading singleton Config if context doesn't have it
    if not config:
        from config import Config
        config = Config.get()
        
    if user_id not in config.admin_ids:
        await update.message.reply_text("⛔ <b>Access Denied.</b>", parse_mode="HTML")
        return

    # Check if a specific slug is provided as an argument
    args = context.args
    target_slug = args[0] if args else None

    # Inform the user that scraper execution has started
    msg = "🚀 <b>Scraper execution triggered!</b>\n\n"
    if target_slug:
        msg += f"Running scraper for slug: <code>{target_slug}</code>..."
    else:
        msg += "Running scraper for all pending titles..."
        
    await update.message.reply_text(msg, parse_mode="HTML")

    # Run run_scrapers.py in a background process
    import sys
    import os
    import asyncio
    
    python_exe = sys.executable
    script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "run_scrapers.py")
    script_path = os.path.abspath(script_path)
    
    async def run_bg_proc():
        try:
            cmd_args = [script_path]
            if target_slug:
                cmd_args.append(target_slug)
                
            proc = await asyncio.create_subprocess_exec(
                python_exe, *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            print(f"[Admin Scrape] Finished. Exit code: {proc.returncode}")
            
            result_msg = (
                f"✅ <b>Scraper Run Finished!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Exit Code: {proc.returncode}\n"
            )
            if target_slug:
                result_msg += f"Title: <code>{target_slug}</code>\n"
            else:
                result_msg += "All pending titles checked.\n"
                
            if proc.returncode != 0:
                result_msg += f"\n⚠️ <b>Error Logs:</b>\n<pre>{stderr.decode()[:2000]}</pre>"
                
            await context.bot.send_message(chat_id=user_id, text=result_msg, parse_mode="HTML")
        except Exception as ex:
            print(f"[Admin Scrape] Process failed: {ex}")
            await context.bot.send_message(chat_id=user_id, text=f"❌ <b>Scraper failed to start:</b> {ex}", parse_mode="HTML")

    asyncio.create_task(run_bg_proc())
