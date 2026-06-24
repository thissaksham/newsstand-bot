"""
Newsstand Bot — /subscriptions handler
Lists active subscriptions grouped by language, with a "get latest" and an
"unsubscribe" button per title.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.operations import get_user_subscriptions, unsubscribe

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


def _render_subscriptions(subs: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    """Build the grouped-by-language text and the per-title button rows
    (📥 get latest · ❌ unsubscribe)."""
    by_lang: dict[str, list[dict]] = {}
    for s in subs:
        by_lang.setdefault(s.get("language", "Other"), []).append(s)

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
    lines.append("📥 = get latest   ·   ❌ = unsubscribe")

    buttons = [
        [
            InlineKeyboardButton(f"📥 {t['name']}", callback_data=f"getlatest:{t['id']}"),
            InlineKeyboardButton("❌", callback_data=f"unsub:{t['id']}"),
        ]
        for t in subs
    ]
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


# ═════════════════════════════════════════════════════════════════════════════
#  /subscriptions — View active subs  (works as a command or a /start button)
# ═════════════════════════════════════════════════════════════════════════════

async def subscriptions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List the user's active subscriptions, grouped by language."""
    if update.callback_query:
        await update.callback_query.answer()

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    db_path = context.bot_data["config"].db_path
    subs = await get_user_subscriptions(db_path, user_id)

    if not subs:
        await context.bot.send_message(
            chat_id,
            "📭 <b>No subscriptions yet.</b>\n\n"
            "Use /subscribe to browse and subscribe to available titles! 🚀",
            parse_mode="HTML",
        )
        return

    text, keyboard = _render_subscriptions(subs)
    await context.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)


# ═════════════════════════════════════════════════════════════════════════════
#  Callback: unsub:{title_id}
# ═════════════════════════════════════════════════════════════════════════════

async def handle_unsub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the inline unsubscribe button."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    db_path = context.bot_data["config"].db_path
    title_id = int(query.data.split(":", 1)[1])

    await unsubscribe(db_path, user_id, title_id)

    subs = await get_user_subscriptions(db_path, user_id)
    if not subs:
        await query.edit_message_text(
            "📭 <b>All subscriptions removed.</b>\n\n"
            "Use /subscribe to add new titles whenever you like!",
            parse_mode="HTML",
        )
        return

    text, keyboard = _render_subscriptions(subs)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
