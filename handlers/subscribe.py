"""
Newsstand Bot — /subscribe, /sub, /unsub handlers
Interactive language → title browser with inline keyboards.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.operations import (
    get_titles_by_language,
    get_all_titles,
    subscribe,
    unsubscribe,
    is_subscribed,
    get_user_subscriptions,
    register_user,
)
from utils.helpers import fuzzy_match_title

logger = logging.getLogger(__name__)

TITLES_PER_PAGE = 8

# ── Language → flag emoji mapping ────────────────────────────────────────────
LANG_FLAGS: dict[str, str] = {
    "english":    "🇬🇧",
    "hindi":      "🇮🇳",
    "tamil":      "🇮🇳",
    "telugu":     "🇮🇳",
    "malayalam":  "🇮🇳",
    "kannada":    "🇮🇳",
    "bengali":    "🇮🇳",
    "marathi":    "🇮🇳",
    "gujarati":   "🇮🇳",
    "punjabi":    "🇮🇳",
    "urdu":       "🇵🇰",
}


def _flag(language: str) -> str:
    return LANG_FLAGS.get(language.lower(), "🌐")


# ═════════════════════════════════════════════════════════════════════════════
#  /subscribe — Interactive browser
# ═════════════════════════════════════════════════════════════════════════════

async def subscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show language picker as inline keyboard."""
    db_path = context.bot_data["config"].db_path
    all_titles = await get_all_titles(db_path)

    # Collect unique languages preserving insertion order
    languages: list[str] = list(dict.fromkeys(t["language"] for t in all_titles))

    if not languages:
        await update.message.reply_text(
            "📭 No titles are configured yet. Check back later!",
            parse_mode="HTML",
        )
        return

    buttons = [
        [InlineKeyboardButton(
            f"{_flag(lang)} {lang.title()}",
            callback_data=f"lang:{lang}",
        )]
        for lang in languages
    ]

    await update.message.reply_text(
        "📰 <b>Subscribe — Choose a Language</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Pick a language to browse available titles:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ── Callback: show titles for a language ─────────────────────────────────────

async def handle_lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    language = query.data.split(":", 1)[1]
    db_path = context.bot_data["config"].db_path
    await _show_titles_page(query, update.effective_user.id, language, page=0, db_path=db_path)


async def handle_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    # callback_data format:  page:lang:{language}:{page_num}
    parts = query.data.split(":")
    language = parts[2]
    page = int(parts[3])
    db_path = context.bot_data["config"].db_path
    await _show_titles_page(query, update.effective_user.id, language, page, db_path=db_path)


async def _show_titles_page(query, user_id: int, language: str, page: int, db_path: str) -> None:
    """Build title list with subscription toggles and pagination."""
    titles = await get_titles_by_language(db_path, language)

    if not titles:
        await query.edit_message_text(
            f"📭 No titles available for <b>{language.title()}</b>.",
            parse_mode="HTML",
        )
        return

    total_pages = max(1, (len(titles) + TITLES_PER_PAGE - 1) // TITLES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * TITLES_PER_PAGE
    end = start + TITLES_PER_PAGE
    page_titles = titles[start:end]

    user_subs = await get_user_subscriptions(db_path, user_id)
    sub_ids = {sub["id"] for sub in user_subs}

    buttons: list[list[InlineKeyboardButton]] = []
    for t in page_titles:
        subscribed = t["id"] in sub_ids
        icon = "✅" if subscribed else "➕"
        buttons.append([
            InlineKeyboardButton(
                f"{icon} {t['name']}",
                callback_data=f"toggle:{t['id']}:{language}:{page}",
            )
        ])

    # Pagination row
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"page:lang:{language}:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"page:lang:{language}:{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    # Back + Done row
    buttons.append([
        InlineKeyboardButton("🔙 Languages", callback_data="lang:__back__"),
        InlineKeyboardButton("✅ Done", callback_data="done"),
    ])

    header = (
        f"{_flag(language)} <b>{language.title()} Titles</b>  "
        f"<i>(page {page + 1}/{total_pages})</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Tap a title to subscribe / unsubscribe:"
    )

    await query.edit_message_text(
        header,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ── Callback: toggle subscription ───────────────────────────────────────────

async def handle_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = update.effective_user.id
    db_path = context.bot_data["config"].db_path
    # toggle:{title_id}:{language}:{page}
    parts = query.data.split(":")
    title_id = parts[1]
    language = parts[2]
    page = int(parts[3])

    # Ensure user is registered before attempting to subscribe
    user = update.effective_user
    await register_user(
        db_path=db_path,
        user_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )

    if await is_subscribed(db_path, user_id, title_id):
        await unsubscribe(db_path, user_id, title_id)
    else:
        await subscribe(db_path, user_id, title_id)

    # Re-render the page to reflect the new state
    await _show_titles_page(query, user_id, language, page, db_path=db_path)


# ── Callback: done ──────────────────────────────────────────────────────────

async def handle_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.edit_message_text(
        "✅ <b>Subscription updated!</b>\n\n"
        "Use /subscriptions to review your picks.\n"
        "Your papers will be delivered automatically each morning. ☀️📰",
        parse_mode="HTML",
    )


# ═════════════════════════════════════════════════════════════════════════════
#  /sub <title_name> — Quick subscribe
# ═════════════════════════════════════════════════════════════════════════════

async def sub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fuzzy-match a title name and subscribe."""
    if not context.args:
        await update.message.reply_text(
            "📌 <b>Usage:</b> <code>/sub title name</code>\n\n"
            "Example: <code>/sub Times of India</code>",
            parse_mode="HTML",
        )
        return

    query_text = " ".join(context.args)
    user = update.effective_user
    user_id = user.id
    db_path = context.bot_data["config"].db_path
    
    await register_user(
        db_path=db_path,
        user_id=user_id,
        username=user.username or "",
        first_name=user.first_name or "",
    )
    
    all_titles = await get_all_titles(db_path)

    match = fuzzy_match_title(query_text, all_titles)

    # Exact / single best match
    if match and isinstance(match, dict):
        title = match
        already = await is_subscribed(db_path, user_id, title["id"])
        if already:
            await update.message.reply_text(
                f"ℹ️ You're already subscribed to <b>{title['name']}</b>.",
                parse_mode="HTML",
            )
            return
        await subscribe(db_path, user_id, title["id"])
        await update.message.reply_text(
            f"✅ Subscribed to <b>{title['name']}</b>!\n"
            "📬 You'll receive it automatically each morning.",
            parse_mode="HTML",
        )
        return

    # Multiple possible matches
    if match and isinstance(match, list):
        buttons = [
            [InlineKeyboardButton(
                f"📰 {t['name']}",
                callback_data=f"quicksub:{t['id']}",
            )]
            for t in match[:3]
        ]
        await update.message.reply_text(
            f"🔍 Multiple matches for <b>{query_text}</b>:\n"
            "Tap the one you meant:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    # No match
    await update.message.reply_text(
        f"❌ No title matching <b>{query_text}</b> found.\n"
        "Use /subscribe to browse all available titles.",
        parse_mode="HTML",
    )


# ═════════════════════════════════════════════════════════════════════════════
#  /unsub <title_name> — Quick unsubscribe
# ═════════════════════════════════════════════════════════════════════════════

async def unsub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fuzzy-match a title name and unsubscribe."""
    if not context.args:
        await update.message.reply_text(
            "📌 <b>Usage:</b> <code>/unsub title name</code>\n\n"
            "Example: <code>/unsub Times of India</code>",
            parse_mode="HTML",
        )
        return

    query_text = " ".join(context.args)
    user = update.effective_user
    user_id = user.id
    db_path = context.bot_data["config"].db_path
    
    await register_user(
        db_path=db_path,
        user_id=user_id,
        username=user.username or "",
        first_name=user.first_name or "",
    )
    
    all_titles = await get_all_titles(db_path)

    match = fuzzy_match_title(query_text, all_titles)

    if match and isinstance(match, dict):
        title = match
        if not await is_subscribed(db_path, user_id, title["id"]):
            await update.message.reply_text(
                f"ℹ️ You're not subscribed to <b>{title['name']}</b>.",
                parse_mode="HTML",
            )
            return
        await unsubscribe(db_path, user_id, title["id"])
        await update.message.reply_text(
            f"🗑️ Unsubscribed from <b>{title['name']}</b>.\n"
            "You won't receive this title anymore.",
            parse_mode="HTML",
        )
        return

    if match and isinstance(match, list):
        buttons = [
            [InlineKeyboardButton(
                f"📰 {t['name']}",
                callback_data=f"quickunsub:{t['id']}",
            )]
            for t in match[:3]
        ]
        await update.message.reply_text(
            f"🔍 Multiple matches for <b>{query_text}</b>:\n"
            "Tap the one you meant:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    await update.message.reply_text(
        f"❌ No title matching <b>{query_text}</b> found.\n"
        "Use /subscriptions to see your active subscriptions.",
        parse_mode="HTML",
    )
