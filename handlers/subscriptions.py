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

# Origin emojis shown on section headers AND title buttons so a user can tell
# what's what: Indian newspapers vs international (downmagaz) papers & magazines.
INDIAN = "🇮🇳"
INTL = "🌍"


def _render_subs(subs: list[dict], mode: str) -> tuple[str, InlineKeyboardMarkup]:
    """Build the subscription list, split by type — Indian newspapers (grouped by
    language), the premium The Hindu / Indian Express category, and International
    news/magazines — with an origin emoji on each section header and every title
    button. ``mode`` is "get" or "remove"."""
    newspapers_by_lang: dict[str, list[dict]] = {}
    premium: list[dict] = []
    magazines: list[dict] = []
    for s in subs:
        category = s.get("category", "Newspaper")
        if category == "Magazine":
            magazines.append(s)
        elif category == "The Hindu/Indian Express":
            premium.append(s)
        else:
            newspapers_by_lang.setdefault(s.get("language") or "Other", []).append(s)

    if mode == "get":
        header, footer, action, prefix = (
            "📥 <b>Get the Latest Edition</b>",
            "Tap a title to fetch its newest edition.", "📥", "getlatest",
        )
    else:
        header, footer, action, prefix = (
            "🗑️ <b>Unsubscribe</b>",
            "Tap a title to unsubscribe from it.", "❌", "unsub",
        )

    lines: list[str] = [header, "━━━━━━━━━━━━━━━━━━━━━━━━━━━━", ""]
    buttons: list[list[InlineKeyboardButton]] = []

    for lang in sorted(newspapers_by_lang):
        lines.append(f"{INDIAN} <b>Indian {lang.title()} Dailies</b>")
        for t in newspapers_by_lang[lang]:
            lines.append(f"  • {t['name']}")
            buttons.append([InlineKeyboardButton(
                f"{action} {INDIAN} {t['name']}", callback_data=f"{prefix}:{t['id']}")])
        lines.append("")

    if premium:
        lines.append("📰 <b>The Hindu / Indian Express</b>")
        for t in premium:
            lines.append(f"  • {t['name']}")
            buttons.append([InlineKeyboardButton(
                f"{action} 📰 {t['name']}", callback_data=f"{prefix}:{t['id']}")])
        lines.append("")

    if magazines:
        lines.append(f"{INTL} <b>International News &amp; Magazines</b>")
        for t in magazines:
            lines.append(f"  • {t['name']}")
            buttons.append([InlineKeyboardButton(
                f"{action} {INTL} {t['name']}", callback_data=f"{prefix}:{t['id']}")])
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
