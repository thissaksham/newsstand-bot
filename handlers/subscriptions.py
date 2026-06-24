"""
Newsstand Bot — /getlatest and /unsubscribe handlers

Both list the user's subscriptions (grouped by language) as full-width buttons:
- /getlatest  → 📥 per title, fetches that title's newest edition
- /unsubscribe → ❌ per title, removes it
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


def _render_subs(subs: list[dict], mode: str) -> tuple[str, InlineKeyboardMarkup]:
    """Build the grouped-by-language text and one full-width button per title.

    ``mode`` is "get" (📥 fetch latest) or "remove" (❌ unsubscribe).
    """
    by_lang: dict[str, list[dict]] = {}
    for s in subs:
        by_lang.setdefault(s.get("language", "Other"), []).append(s)

    if mode == "get":
        header = "📥 <b>Get the Latest Edition</b>"
        footer = "Tap a title to fetch its newest edition."
        buttons = [
            [InlineKeyboardButton(f"📥 {t['name']}", callback_data=f"getlatest:{t['id']}")]
            for t in subs
        ]
    else:
        header = "🗑️ <b>Unsubscribe</b>"
        footer = "Tap a title to unsubscribe from it."
        buttons = [
            [InlineKeyboardButton(f"❌ {t['name']}", callback_data=f"unsub:{t['id']}")]
            for t in subs
        ]

    lines: list[str] = [header, "━━━━━━━━━━━━━━━━━━━━━━━━━━━━", ""]
    for lang, titles in by_lang.items():
        lines.append(f"{_flag(lang)} <b>{lang.title()}</b>")
        for t in titles:
            lines.append(f"  • {t['name']}")
        lines.append("")
    lines.append(f"<i>{footer}</i>")

    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def _show_subs(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str) -> None:
    """Send the subscription list in the given mode. Works as a command or a
    /start button callback."""
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
            "Use /subscribe to add some! 🚀",
            parse_mode="HTML",
        )
        return

    text, keyboard = _render_subs(subs, mode)
    await context.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)


# ── Commands ─────────────────────────────────────────────────────────────────

async def getlatest_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/getlatest — list subscriptions with a 📥 button per title."""
    await _show_subs(update, context, "get")


async def unsubscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unsubscribe — list subscriptions with a ❌ button per title."""
    await _show_subs(update, context, "remove")


# ── Callback: unsub:{title_id} (from the /unsubscribe list) ──────────────────

async def handle_unsub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove a subscription and refresh the unsubscribe list in place."""
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

    text, keyboard = _render_subs(subs, "remove")
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
