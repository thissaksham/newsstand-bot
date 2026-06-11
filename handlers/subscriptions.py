"""
Newsstand Bot — /subscriptions handler
Lists active subscriptions grouped by language with unsubscribe buttons.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.operations import get_user_subscriptions, unsubscribe, get_all_titles

logger = logging.getLogger(__name__)

# ── Language → flag emoji mapping ────────────────────────────────────────────
LANG_FLAGS: dict[str, str] = {
    "english":   "🇬🇧",
    "hindi":     "🇮🇳",
    "tamil":     "🇮🇳",
    "telugu":    "🇮🇳",
    "malayalam": "🇮🇳",
    "kannada":   "🇮🇳",
    "bengali":   "🇮🇳",
    "marathi":   "🇮🇳",
    "gujarati":  "🇮🇳",
    "punjabi":   "🇮🇳",
    "urdu":      "🇵🇰",
}


def _flag(language: str) -> str:
    return LANG_FLAGS.get(language.lower(), "🌐")


# ═════════════════════════════════════════════════════════════════════════════
#  /subscriptions — View active subs
# ═════════════════════════════════════════════════════════════════════════════

async def subscriptions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List the user's active subscriptions, grouped by language."""
    user_id = update.effective_user.id
    db_path = context.bot_data["config"].db_path
    subs = await get_user_subscriptions(db_path, user_id)

    if not subs:
        await update.message.reply_text(
            "📭 <b>No subscriptions yet.</b>\n\n"
            "Use /subscribe to browse titles or /packs to grab a curated bundle! 🚀",
            parse_mode="HTML",
        )
        return

    # Group by language
    by_lang: dict[str, list[dict]] = {}
    for s in subs:
        lang = s.get("language", "Other")
        by_lang.setdefault(lang, []).append(s)

    # Build text
    lines: list[str] = [
        f"📋 <b>Your Subscriptions</b> ({len(subs)} title{'s' if len(subs) != 1 else ''})",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    for lang, titles in by_lang.items():
        lines.append(f"{_flag(lang)} <b>{lang.title()}</b>")
        for t in titles:
            lines.append(f"  • {t['name']}")
        lines.append("")

    # Build unsubscribe buttons
    buttons = [
        [InlineKeyboardButton(
            f"❌ {t['name']}",
            callback_data=f"unsub:{t['id']}",
        )]
        for t in subs
    ]

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


# ═════════════════════════════════════════════════════════════════════════════
#  Callback: unsub:{title_id}
# ═════════════════════════════════════════════════════════════════════════════

async def handle_unsub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the inline unsubscribe button."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    db_path = context.bot_data["config"].db_path
    title_id = query.data.split(":", 1)[1]

    await unsubscribe(db_path, user_id, title_id)

    # Re-fetch to rebuild the view
    subs = await get_user_subscriptions(db_path, user_id)

    if not subs:
        await query.edit_message_text(
            "📭 <b>All subscriptions removed.</b>\n\n"
            "Use /subscribe to add new titles whenever you like!",
            parse_mode="HTML",
        )
        return

    # Rebuild grouped list
    by_lang: dict[str, list[dict]] = {}
    for s in subs:
        lang = s.get("language", "Other")
        by_lang.setdefault(lang, []).append(s)

    lines: list[str] = [
        f"📋 <b>Your Subscriptions</b> ({len(subs)} title{'s' if len(subs) != 1 else ''})",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    for lang, titles in by_lang.items():
        lines.append(f"{_flag(lang)} <b>{lang.title()}</b>")
        for t in titles:
            lines.append(f"  • {t['name']}")
        lines.append("")

    buttons = [
        [InlineKeyboardButton(
            f"❌ {t['name']}",
            callback_data=f"unsub:{t['id']}",
        )]
        for t in subs
    ]

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
