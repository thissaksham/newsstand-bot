"""
Newsstand Bot — /start and /help handlers
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.operations import register_user

# Inline buttons shown under the /start welcome for tap-based onboarding.
START_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📰 Subscribe", callback_data="start_subscribe")],
    [
        InlineKeyboardButton("📋 My Subscriptions", callback_data="start_mysubs"),
        InlineKeyboardButton("❓ Help", callback_data="start_help"),
    ],
])

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
        "│ 2️⃣  /get — Fetch any edition on demand\n"
        "└─────────────────────────────\n\n"
        "📖 <b>All Commands</b>\n\n"
        "  /subscribe — Subscribe &amp; unsubscribe (interactive browser)\n"
        "  /subscriptions — View &amp; manage your subscriptions\n"
        "  /get — Fetch any newspaper or magazine edition on demand\n"
        "  /help — Command reference\n\n"
        "Let's get you set up — tap a button below 👇"
    )

    await update.message.reply_text(welcome, parse_mode="HTML", reply_markup=START_KEYBOARD)


# ── /help ────────────────────────────────────────────────────────────────────

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send detailed command reference grouped by category. Works as the /help
    command or the ❓ Help button on /start."""
    if update.callback_query:
        await update.callback_query.answer()
    text = (
        "📚 <b>Newsstand Bot — Command Reference</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "🔎 <b>Browsing &amp; Subscriptions</b>\n"
        "  /subscribe — Interactive browser. Subscribe to newspapers (by "
        "language) or search magazines by name. Tap a subscribed title again "
        "to unsubscribe.\n"
        "  /subscriptions — View your active subscriptions; tap 📥 to grab the "
        "latest edition or ❌ to unsubscribe.\n\n"

        "📰 <b>Reading &amp; Retrieval</b>\n"
        "  /get — Fetch any edition on demand: pick a newspaper and a date, or "
        "search a magazine and pick an issue.\n\n"

        "💡 <i>New editions are delivered to you automatically as soon as "
        "they're published.</i>"
    )

    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode="HTML")
