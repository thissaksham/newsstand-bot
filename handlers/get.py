"""
Newsstand Bot — /get handler

Interactive "fetch any edition" browser. Pick a newspaper **language → title →
date**, or **search magazines → pick one → pick an issue**. Nothing comes from an
archive: newspaper dates resolve to a live link via the scrapers'
``find_download_link``, and magazine issues come from a live downmagaz tag scrape
(``scrape_magazine_tag`` + ``get_download_links``). Everything is link-based.
"""

import logging
from datetime import date, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from config import Config
from utils.helpers import format_date, format_date_long, get_today, html_escape, magazine_date_label
from scrapers import find_newspaper_link
from scrapers.downmagaz_net import (
    search_magazines, scrape_magazine_tag, matches_version, get_download_links,
)

logger = logging.getLogger(__name__)

# ── Conversation States ──────────────────────────────────────────────────────
(SELECT_LANGUAGE, SELECT_TITLE, SELECT_DATE,
 AWAIT_MAG_NAME, SELECT_MAGAZINE, SELECT_EDITION) = range(6)

TITLES_PER_PAGE = 8
DATES_PER_PAGE = 8
EDITIONS_PER_PAGE = 8
DATE_WINDOW_DAYS = 30  # how far back the date picker can go


# ── Helpers ──────────────────────────────────────────────────────────────────

def _download_link_label(url: str) -> str:
    """Human-friendly source label for a download URL."""
    url_l = url.lower()
    if "drive.google.com" in url_l or "google.com" in url_l:
        return "Google Drive"
    if "indiags.com" in url_l:
        return "indiags.com"
    return "source"


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
        [InlineKeyboardButton(f"🇮🇳 Indian {lang.title()} Dailies", callback_data=f"get_lang_{lang}")]
        for lang in languages
    ]
    buttons.append([InlineKeyboardButton(
        "📰 The Hindu / Indian Express", callback_data="get_cat_The Hindu/Indian Express")])
    buttons.append([InlineKeyboardButton(
        "🌍 International News & Magazines", callback_data="get_magazines")])

    await update.message.reply_text(
        "📰 <b>Get an Edition</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Pick an Indian newspaper language, or search international news &amp; magazines:",
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
        f"🇮🇳 <b>Indian {html_escape(language.title())} Dailies</b>  "
        f"<i>(page {page + 1}/{total_pages})</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select a title:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )
    return SELECT_TITLE


# ── Step 2b: category → choose a title (The Hindu / Indian Express) ──────────

async def get_category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    category = query.data[len("get_cat_"):]
    context.user_data["get_category"] = category
    return await _show_category_titles_page(query, context, category, 0)


async def handle_category_titles_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    page = int(query.data[len("get_ctpage_"):])
    category = context.user_data.get("get_category", "")
    return await _show_category_titles_page(query, context, category, page)


async def _show_category_titles_page(query, context, category: str, page: int) -> int:
    titles = [
        t for t in Config.get().titles
        if getattr(t, "category", "Newspaper") == category
    ]
    if not titles:
        await query.edit_message_text(f"📭 No titles available for <b>{html_escape(category)}</b>.", parse_mode="HTML")
        return ConversationHandler.END

    context.user_data["get_titles"] = titles

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
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"get_ctpage_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"get_ctpage_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔙 Cancel", callback_data="get_cancel")])

    label = "📰 The Hindu / Indian Express" if category == "The Hindu/Indian Express" else html_escape(category)
    await query.edit_message_text(
        f"{label}\n"
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
        result = await find_newspaper_link(title.name, title.scrape_website, title.source_url, [d])
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
        f"📰 <b>{safe_name}</b> — {format_date_long(edition_date)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Here is your edition:\n"
        f'<a href="{html_escape(link)}">⬇️ Download ({html_escape(_download_link_label(link))})</a>',
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    return ConversationHandler.END


# ── Magazines: search → pick magazine → pick issue → links ───────────────────

async def get_magazines_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🌍 <b>International News &amp; Magazines</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Type the name of an international newspaper or magazine "
        "(e.g. <i>The Economist</i>, <i>The Washington Post</i>):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="get_cancel")]]),
    )
    return AWAIT_MAG_NAME


async def handle_get_mag_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query_text = update.message.text.strip()
    safe_query = html_escape(query_text)
    status = await update.message.reply_text("🔍 Searching… ⏳")
    try:
        results = await search_magazines(query_text)
    except Exception:
        logger.exception("[/get] magazine search failed")
        results = []
    try:
        await status.delete()
    except Exception:
        pass

    if not results:
        await update.message.reply_text(
            f"❌ No international titles matched <b>{safe_query}</b>. Type another name:",
            parse_mode="HTML",
        )
        return AWAIT_MAG_NAME

    context.user_data["get_mag_results"] = results
    buttons = []
    for i, m in enumerate(results[:8]):
        label = m["edition_name"]
        countries = m.get("countries") or []
        if countries:
            label = f"{label} ({', '.join(countries)})"
        buttons.append([InlineKeyboardButton(f"📖 {label}", callback_data=f"getmag_{i}")])
    buttons.append([InlineKeyboardButton("🔙 Cancel", callback_data="get_cancel")])

    await update.message.reply_text(
        f"🔍 <b>Results for '{safe_query}'</b>:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Pick a title:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )
    return SELECT_MAGAZINE


async def handle_get_mag_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = int(query.data[len("getmag_"):])
    results = context.user_data.get("get_mag_results", [])
    if idx < 0 or idx >= len(results):
        await query.edit_message_text("⌛ Session expired. Please run /get again.")
        return ConversationHandler.END

    m = results[idx]
    await query.edit_message_text(f"⏳ Loading issues of <b>{html_escape(m['edition_name'])}</b>…", parse_mode="HTML")
    try:
        posts = await scrape_magazine_tag(m["tag_url"])
    except Exception:
        logger.exception("[/get] magazine tag scrape failed")
        posts = []

    issues = [p for p in posts if matches_version(p["title"], m.get("version"))]
    issues.sort(key=lambda p: p["date"], reverse=True)
    if not issues:
        await query.edit_message_text(f"📭 No issues found for <b>{html_escape(m['edition_name'])}</b>.", parse_mode="HTML")
        return ConversationHandler.END

    context.user_data["get_mag_editions"] = issues
    context.user_data["get_mag_name"] = m["edition_name"]
    return await _show_mag_editions_page(query, context, 0)


async def handle_get_mpage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    page = int(query.data[len("getmpage_"):])
    return await _show_mag_editions_page(query, context, page)


async def _show_mag_editions_page(query, context, page: int) -> int:
    issues = context.user_data.get("get_mag_editions", [])
    name = context.user_data.get("get_mag_name", "Magazine")
    if not issues:
        await query.edit_message_text("⌛ Session expired. Please run /get again.")
        return ConversationHandler.END

    total_pages = max(1, (len(issues) + EDITIONS_PER_PAGE - 1) // EDITIONS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * EDITIONS_PER_PAGE
    page_issues = issues[start:start + EDITIONS_PER_PAGE]

    buttons = [
        [InlineKeyboardButton(magazine_date_label(p["title"], p["date"]), callback_data=f"getmed_{start + i}")]
        for i, p in enumerate(page_issues)
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Newer", callback_data=f"getmpage_{page - 1}"))
    if start + EDITIONS_PER_PAGE < len(issues):
        nav.append(InlineKeyboardButton("Older ➡️", callback_data=f"getmpage_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔙 Cancel", callback_data="get_cancel")])

    await query.edit_message_text(
        f"📖 <b>{html_escape(name)}</b>  <i>(page {page + 1}/{total_pages})</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Choose an issue to fetch:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )
    return SELECT_EDITION


async def handle_get_edition_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("Fetching… ⏳")
    idx = int(query.data[len("getmed_"):])
    issues = context.user_data.get("get_mag_editions", [])
    name = context.user_data.get("get_mag_name", "Magazine")
    if idx < 0 or idx >= len(issues):
        await query.edit_message_text("⌛ Session expired. Please run /get again.")
        return ConversationHandler.END

    issue = issues[idx]
    safe_name = html_escape(name)
    label = magazine_date_label(issue["title"], issue["date"])
    await query.edit_message_text(f"⏳ Fetching <b>{safe_name}</b> — {html_escape(label)}…", parse_mode="HTML")

    try:
        links = await get_download_links(issue["url"])
    except Exception as e:
        logger.exception("[/get] magazine link scrape failed")
        await query.edit_message_text(f"❌ Fetch failed: {html_escape(str(e))}", parse_mode="HTML")
        return ConversationHandler.END

    if not links:
        await query.edit_message_text(
            f"📭 No download links available yet for <b>{html_escape(issue['title'])}</b>. Try again later.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    links_html = "".join(
        f'• <a href="{html_escape(href)}">Download via {html_escape(domain)}</a>\n'
        for domain, href in links
    )
    await query.edit_message_text(
        f"📖 <b>{safe_name}</b> — {html_escape(label)}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Download Links:\n{links_html}",
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
            CallbackQueryHandler(get_category_selected, pattern="^get_cat_"),
            CallbackQueryHandler(get_magazines_prompt, pattern="^get_magazines$"),
        ],
        SELECT_TITLE: [
            CallbackQueryHandler(handle_titles_page_callback, pattern="^get_tpage_"),
            CallbackQueryHandler(handle_category_titles_page_callback, pattern="^get_ctpage_"),
            CallbackQueryHandler(get_title_selected, pattern="^get_title_"),
        ],
        SELECT_DATE: [
            CallbackQueryHandler(handle_dates_page_callback, pattern="^get_dpage_"),
            CallbackQueryHandler(get_date_selected, pattern="^get_date_"),
        ],
        AWAIT_MAG_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_get_mag_search),
        ],
        SELECT_MAGAZINE: [
            CallbackQueryHandler(handle_get_mag_selected, pattern="^getmag_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_get_mag_search),
        ],
        SELECT_EDITION: [
            CallbackQueryHandler(handle_get_mpage, pattern="^getmpage_"),
            CallbackQueryHandler(handle_get_edition_selected, pattern="^getmed_"),
        ],
    },
    fallbacks=[CallbackQueryHandler(get_cancel, pattern="^get_cancel$")],
)
