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
        "│ 2️⃣  /get &lt;title&gt; — Grab a specific paper\n"
        "└─────────────────────────────\n\n"
        "📖 <b>All Commands</b>\n\n"
        "  <b>Browsing</b>\n"
        "  /subscribe — Interactive title browser\n\n"
        "  <b>Subscriptions</b>\n"
        "  /sub &lt;title&gt; — Quick subscribe\n"
        "  /unsub &lt;title&gt; or /unsubscribe — Quick unsubscribe\n"
        "  /subscriptions — View active subs\n\n"
        "  <b>Reading</b>\n"
        "  /get &lt;title&gt; [date] — Fetch an edition\n\n"
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
        "  /get &lt;title&gt; — Today's edition of a title\n"
        "  /get &lt;title&gt; &lt;DD-MM-YYYY&gt; — Archived edition\n\n"
        "💡 <i>Tip: You can use partial names with /sub and /get — "
        "the bot will fuzzy-match them for you!</i>"
    )

    await update.message.reply_text(text, parse_mode="HTML")
