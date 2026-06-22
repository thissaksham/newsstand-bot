"""
Newsstand Bot — /get handler

Interactive "fetch any edition" flow that mirrors the /subscribe browser, but
the final step is a date instead of a subscribe toggle. It does NOT read the
archive — it looks up the chosen title+date's download link live (reusing the
scraper modules' ``find_download_link``) and sends the link, the same way a
normal subscription delivery does.
"""

import importlib
import logging
from datetime import date, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
)

from config import Config
from utils.helpers import format_date, get_today, html_escape

logger = logging.getLogger(__name__)

# ── Conversation States ──────────────────────────────────────────────────────
SELECT_LANGUAGE, SELECT_TITLE, SELECT_DATE = range(3)

TITLES_PER_PAGE = 8
DATES_PER_PAGE = 8
DATE_WINDOW_DAYS = 30  # how far back the date picker can go

LANG_FLAGS: dict[str, str] = {
    "english": "🇬🇧", "hindi": "🇮🇳", "tamil": "🇮🇳", "telugu": "🇮🇳",
    "malayalam": "🇮🇳", "kannada": "🇮🇳", "bengali": "🇮🇳", "marathi": "🇮🇳",
    "gujarati": "🇮🇳", "punjabi": "🇮🇳", "urdu": "🇵🇰",
}


def _flag(language: str) -> str:
    return LANG_FLAGS.get(language.lower(), "🌐")


# ── Step 1: /get → choose a language ─────────────────────────────────────────

async def get_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the /get conversation and ask for a language (from config.yaml)."""
    languages = list(dict.fromkeys(
        t.language for t in Config.get().titles
        if getattr(t, "category", "Newspaper") == "Newspaper"
    ))

    if not languages:
        await update.message.reply_text("No newspapers are configured.")
        return ConversationHandler.END

    buttons = [
        [InlineKeyboardButton(f"{_flag(lang)} {lang.title()}", callback_data=f"get_lang_{lang}")]
        for lang in languages
    ]

    await update.message.reply_text(
        "📰 <b>Get a Newspaper</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select a language:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )
    return SELECT_LANGUAGE


# ── Step 2: language → choose a title ────────────────────────────────────────

async def get_language_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    language = query.data[len("get_lang_"):]
    context.user_data["get_language"] = language
    return await _show_titles_page(query, context, language, 0)


async def handle_titles_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    page = int(query.data[len("get_tpage_"):])
    language = context.user_data.get("get_language", "")
    return await _show_titles_page(query, context, language, page)


async def _show_titles_page(query, context, language: str, page: int) -> int:
    titles = [
        t for t in Config.get().get_titles_by_language(language)
        if getattr(t, "category", "Newspaper") == "Newspaper"
    ]
    if not titles:
        await query.edit_message_text(f"📭 No titles available for <b>{html_escape(language.title())}</b>.", parse_mode="HTML")
        return ConversationHandler.END

    context.user_data["get_titles"] = titles  # cache for selection by index

    total_pages = max(1, (len(titles) + TITLES_PER_PAGE - 1) // TITLES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * TITLES_PER_PAGE
    page_titles = titles[start:start + TITLES_PER_PAGE]

    buttons = [
        [InlineKeyboardButton(t.name, callback_data=f"get_title_{start + i}")]
        for i, t in enumerate(page_titles)
    ]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"get_tpage_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"get_tpage_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔙 Cancel", callback_data="get_cancel")])

    await query.edit_message_text(
        f"{_flag(language)} <b>{html_escape(language.title())} Newspapers</b>  "
        f"<i>(page {page + 1}/{total_pages})</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select a title:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )
    return SELECT_TITLE


# ── Step 3: title → choose a date ────────────────────────────────────────────

async def get_title_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = int(query.data[len("get_title_"):])
    titles = context.user_data.get("get_titles", [])
    if idx < 0 or idx >= len(titles):
        await query.edit_message_text("⌛ Session expired. Please run /get again.")
        return ConversationHandler.END
    context.user_data["get_title_idx"] = idx
    return await _show_dates_page(query, context, 0)


async def handle_dates_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    page = int(query.data[len("get_dpage_"):])
    return await _show_dates_page(query, context, page)


async def _show_dates_page(query, context, page: int, note: str = "") -> int:
    titles = context.user_data.get("get_titles", [])
    idx = context.user_data.get("get_title_idx")
    if idx is None or idx >= len(titles):
        await query.edit_message_text("⌛ Session expired. Please run /get again.")
        return ConversationHandler.END
    title = titles[idx]

    today = get_today()
    all_dates = [today - timedelta(days=i) for i in range(DATE_WINDOW_DAYS)]
    start = page * DATES_PER_PAGE
    page_dates = all_dates[start:start + DATES_PER_PAGE]

    buttons, row = [], []
    for d in page_dates:
        row.append(InlineKeyboardButton(format_date(d), callback_data=f"get_date_{d.isoformat()}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Newer", callback_data=f"get_dpage_{page - 1}"))
    if start + DATES_PER_PAGE < len(all_dates):
        nav.append(InlineKeyboardButton("Older ➡️", callback_data=f"get_dpage_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔙 Cancel", callback_data="get_cancel")])

    header = f"📅 <b>{html_escape(title.name)}</b>\n"
    if note:
        header += f"{note}\n"
    header += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nChoose a date to fetch:"

    await query.edit_message_text(header, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    return SELECT_DATE


# ── Step 4: date → scrape live and deliver the PDF ───────────────────────────

async def get_date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("Fetching… ⏳")

    titles = context.user_data.get("get_titles", [])
    idx = context.user_data.get("get_title_idx")
    if idx is None or idx >= len(titles):
        await query.edit_message_text("⌛ Session expired. Please run /get again.")
        return ConversationHandler.END

    title = titles[idx]
    d = date.fromisoformat(query.data[len("get_date_"):])
    friendly = format_date(d)
    safe_name = html_escape(title.name)

    if not title.source_url or not title.scrape_website:
        await query.edit_message_text(f"❌ <b>{safe_name}</b> has no source configured.", parse_mode="HTML")
        return ConversationHandler.END

    await query.edit_message_text(f"⏳ Fetching <b>{safe_name}</b> for {friendly}…", parse_mode="HTML")

    try:
        module = importlib.import_module(f"scrapers.{title.scrape_website}")
    except ImportError:
        await query.edit_message_text(f"❌ Scraper <code>{html_escape(title.scrape_website)}</code> not found.", parse_mode="HTML")
        return ConversationHandler.END

    try:
        result = await module.find_download_link(title.source_url, [d])
    except Exception as e:
        logger.exception("[/get] link lookup failed for %s %s", title.slug, d)
        await query.edit_message_text(f"❌ Fetch failed: {html_escape(str(e))}", parse_mode="HTML")
        return ConversationHandler.END

    if not result:
        # Not published / not on the source for that date — let them try another.
        return await _show_dates_page(
            query, context, 0,
            note=f"📭 <i>No edition found for {friendly}. Try another date.</i>",
        )

    edition_date, link = result
    await query.edit_message_text(
        f"📰 <b>{safe_name}</b> — {format_date(edition_date)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Here is your edition:\n"
        f'<a href="{html_escape(link)}">⬇️ Download (Google Drive)</a>',
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    return ConversationHandler.END


async def get_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Cancelled.")
    return ConversationHandler.END


get_conversation_handler = ConversationHandler(
    entry_points=[CommandHandler("get", get_start)],
    states={
        SELECT_LANGUAGE: [
            CallbackQueryHandler(get_language_selected, pattern="^get_lang_"),
        ],
        SELECT_TITLE: [
            CallbackQueryHandler(handle_titles_page_callback, pattern="^get_tpage_"),
            CallbackQueryHandler(get_title_selected, pattern="^get_title_"),
        ],
        SELECT_DATE: [
            CallbackQueryHandler(handle_dates_page_callback, pattern="^get_dpage_"),
            CallbackQueryHandler(get_date_selected, pattern="^get_date_"),
        ],
    },
    fallbacks=[CallbackQueryHandler(get_cancel, pattern="^get_cancel$")],
)
