"""
Newsstand Bot — /subscribe, /sub, /unsub handlers
Interactive category → title browser with inline keyboards.
Supports newspaper languages and downmagaz.net magazine searches.
"""

import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from database.operations import (
    get_titles_by_language,
    get_all_titles,
    subscribe,
    unsubscribe,
    is_subscribed,
    get_user_subscriptions,
    register_user,
    get_title_by_slug,
    add_title,
    search_titles,
)
from utils.helpers import fuzzy_match_title
from scrapers.downmagaz_net import (
    search_magazines,
    scrape_magazine_tag,
    get_download_links,
    get_magazine_tag_and_version,
    matches_version,
)

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

# ── Conversation States ──────────────────────────────────────────────────────
SELECT_CATEGORY, AWAITING_MAGAZINE_NAME = range(2)

def _flag(language: str) -> str:
    return LANG_FLAGS.get(language.lower(), "🌐")


# ═════════════════════════════════════════════════════════════════════════════
#  /subscribe — Interactive category picker
# ═════════════════════════════════════════════════════════════════════════════

async def subscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show category/language picker as inline keyboard."""
    db_path = context.bot_data["config"].db_path
    all_titles = await get_all_titles(db_path)

    # Collect unique languages for newspapers only
    languages: list[str] = list(dict.fromkeys(
        t["language"] for t in all_titles 
        if t.get("category", "Newspaper") == "Newspaper"
    ))

    buttons = []
    # 1. Newspaper languages
    for lang in languages:
        buttons.append([InlineKeyboardButton(
            f"{_flag(lang)} {lang.title()} Dailies",
            callback_data=f"lang:{lang}",
        )])
        
    # 2. Magazines category
    buttons.append([InlineKeyboardButton(
        "📖 Magazines",
        callback_data="cat:magazine",
    )])

    await update.message.reply_text(
        "📰 <b>Subscribe — Choose a Category</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Pick a category or language to browse available titles:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return SELECT_CATEGORY


# ── Callback: show titles for a language ─────────────────────────────────────

async def handle_lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = query.data.split(":", 1)[1]

    if language == "__back__":
        # Re-show the main category picker
        db_path = context.bot_data["config"].db_path
        all_titles = await get_all_titles(db_path)
        languages: list[str] = list(dict.fromkeys(
            t["language"] for t in all_titles 
            if t.get("category", "Newspaper") == "Newspaper"
        ))

        buttons = []
        for lang in languages:
            buttons.append([InlineKeyboardButton(
                f"{_flag(lang)} {lang.title()} Dailies",
                callback_data=f"lang:{lang}",
            )])
        buttons.append([InlineKeyboardButton(
            "📖 Magazines",
            callback_data="cat:magazine",
        )])

        await query.edit_message_text(
            "📰 <b>Subscribe — Choose a Category</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Pick a category or language to browse available titles:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return SELECT_CATEGORY

    db_path = context.bot_data["config"].db_path
    await _show_titles_page(query, update.effective_user.id, language, page=0, db_path=db_path)
    return SELECT_CATEGORY


async def handle_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    parts = query.data.split(":")
    language = parts[2]
    page = int(parts[3])
    db_path = context.bot_data["config"].db_path
    await _show_titles_page(query, update.effective_user.id, language, page, db_path=db_path)
    return SELECT_CATEGORY


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
        InlineKeyboardButton("🔙 Categories", callback_data="lang:__back__"),
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

async def handle_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    db_path = context.bot_data["config"].db_path
    parts = query.data.split(":")
    title_id = int(parts[1])
    language = parts[2]
    page = int(parts[3])

    # Ensure user is registered
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

    await _show_titles_page(query, user_id, language, page, db_path=db_path)
    return SELECT_CATEGORY


# ── Callback: done ──────────────────────────────────────────────────────────

async def handle_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.edit_message_text(
            "✅ <b>Subscription updated!</b>\n\n"
            "Use /subscriptions to review your picks.\n"
            "Your papers will be delivered automatically each morning. ☀️📰",
            parse_mode="HTML",
        )
    return ConversationHandler.END


# ── Callback: Magazines category selected ────────────────────────────────────

async def handle_cat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.edit_message_text(
        "📖 <b>Subscribe to a Magazine</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Please type the name of the magazine you want to search for (e.g. <i>The Economist</i>):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="lang:__back__")
        ]])
    )
    return AWAITING_MAGAZINE_NAME


# ── Message: Text search query received ──────────────────────────────────────

# ── Message: Text search query received ──────────────────────────────────────

async def handle_magazine_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query_text = update.message.text.strip()
    db_path = context.bot_data["config"].db_path
    
    status_msg = await update.message.reply_text("🔍 Searching for matching magazines... ⏳")
    
    # 1. Search titles table in DB for matching magazines
    db_results = await search_titles(db_path, query_text)
    db_magazines = [t for t in db_results if t.get("category") == "Magazine"]
    
    # 2. Search tags via scraper
    web_results = await search_magazines(query_text)
    
    # Merge options, prioritizing DB matches, keeping unique by slug
    merged_results = []
    seen_slugs = set()
    
    # Create a map of slug -> countries from web results
    web_countries_map = {e["slug"]: e["countries"] for e in web_results}
    
    # 1. Add DB magazines
    for t in db_magazines:
        slug = t["slug"]
        if slug not in seen_slugs:
            merged_results.append({
                "is_db": True,
                "title_id": t["id"],
                "edition_name": t["name"],
                "slug": slug,
                "countries": web_countries_map.get(slug, [])
            })
            seen_slugs.add(slug)
            
    # 2. Add web results
    for e in web_results:
        slug = e["slug"]
        if slug not in seen_slugs:
            merged_results.append({
                "is_db": False,
                "edition_name": e["edition_name"],
                "tag_name": e["tag_name"],
                "tag_url": e["tag_url"],
                "slug": e["slug"],
                "version": e["version"],
                "countries": e["countries"]
            })
            seen_slugs.add(slug)
            
    await status_msg.delete()
    
    if not merged_results:
        await update.message.reply_text(
            f"❌ No matching magazines found for <b>{query_text}</b>.\n"
            "Please check the spelling and try again:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back to Categories", callback_data="lang:__back__")
            ]])
        )
        return AWAITING_MAGAZINE_NAME

    # Save to context user_data to resolve callback limits
    context.user_data["search_results"] = merged_results

    # Show top 8 options
    buttons = []
    for idx, item in enumerate(merged_results[:8]):
        display_name = item["edition_name"]
        countries = item.get("countries", [])
        if countries:
            display_name = f"{display_name} ({', '.join(countries)})"
        buttons.append([InlineKeyboardButton(f"📖 {display_name}", callback_data=f"submag:{idx}")])
        
    buttons.append([
        InlineKeyboardButton("🔙 Back to Categories", callback_data="lang:__back__")
    ])
    
    await update.message.reply_text(
        f"🔍 <b>Search Results for '{query_text}'</b>:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select the magazine you want to subscribe to:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return AWAITING_MAGAZINE_NAME


# ── Callback: Select a magazine from search results ──────────────────────────

async def handle_submag_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    parts = query.data.split(":", 1)
    idx = int(parts[1])
    
    search_results = context.user_data.get("search_results", [])
    if idx < 0 or idx >= len(search_results):
        await query.edit_message_text("❌ Session expired or invalid selection. Please try searching again.")
        return ConversationHandler.END
        
    item = search_results[idx]
    db_path = context.bot_data["config"].db_path
    user_id = update.effective_user.id
    
    # Ensure user is registered
    user = update.effective_user
    await register_user(
        db_path=db_path,
        user_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )

    if item["is_db"]:
        title_id = item["title_id"]
        title_name = item["edition_name"]
        slug = item["slug"]
    else:
        title_name = item["edition_name"]
        slug = item["slug"]
        
        # Check if slug exists in DB
        title = await get_title_by_slug(db_path, slug)
        if title:
            title_id = title["id"]
        else:
            title_id = await add_title(
                db_path=db_path,
                name=title_name,
                slug=slug,
                language="English",
                category="Magazine",
                source="downmagaz_net"
            )
            
    # Subscribe user
    already = await is_subscribed(db_path, user_id, title_id)
    if already:
        await query.edit_message_text(
            f"ℹ️ You're already subscribed to <b>{title_name}</b>.",
            parse_mode="HTML"
        )
    else:
        await subscribe(db_path, user_id, title_id)
        
        latest_edition_info = ""
        try:
            from urllib.parse import quote
            tag_name_for_url, version = get_magazine_tag_and_version(title_name, slug)
            
            tag_url = f"https://downmagaz.net/tags/{quote(tag_name_for_url.lower())}/"
            posts = await scrape_magazine_tag(tag_url)
            if posts:
                matching_posts = [p for p in posts if matches_version(p["title"], version)]
                if matching_posts:
                    latest_post = matching_posts[0]
                    links = await get_download_links(latest_post["url"])
                    if links:
                        links_html = ""
                        for domain, href in links:
                            links_html += f"• <a href=\"{href}\">Download via {domain}</a>\n"
                        
                        latest_edition_info = (
                            f"\n\n🔥 <b>Latest Edition Available:</b>\n"
                            f"👉 <b>{latest_post['title']}</b>\n\n"
                            f"Download Links:\n{links_html}"
                        )
        except Exception as e:
            logger.error(f"Error fetching latest edition for {title_name}: {e}")
            
        await query.edit_message_text(
            f"✅ Subscribed to <b>{title_name}</b>!\n\n"
            f"Whenever a new edition comes, we'll send you the download links automatically! 📖🚀"
            f"{latest_edition_info}",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        
    return ConversationHandler.END


# ═════════════════════════════════════════════════════════════════════════════
#  Conversation Handler Definition
# ═════════════════════════════════════════════════════════════════════════════

subscribe_conversation_handler = ConversationHandler(
    entry_points=[
        CommandHandler("subscribe", subscribe_handler),
    ],
    states={
        SELECT_CATEGORY: [
            CallbackQueryHandler(handle_lang_callback, pattern="^lang:"),
            CallbackQueryHandler(handle_cat_callback, pattern="^cat:"),
            CallbackQueryHandler(handle_toggle_callback, pattern="^toggle:"),
            CallbackQueryHandler(handle_page_callback, pattern="^page:"),
            CallbackQueryHandler(handle_done_callback, pattern="^done$"),
        ],
        AWAITING_MAGAZINE_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_magazine_search),
            CallbackQueryHandler(handle_lang_callback, pattern="^lang:"),
            CallbackQueryHandler(handle_submag_callback, pattern="^submag:"),
        ],
    },
    fallbacks=[
        CommandHandler("cancel", handle_done_callback),
        CallbackQueryHandler(handle_done_callback, pattern="^done$"),
    ],
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
    matches = fuzzy_match_title(query_text, all_titles)

    if len(matches) == 1:
        title, score = matches[0]
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
            "📬 You'll receive it automatically.",
            parse_mode="HTML",
        )
        return

    if len(matches) > 1:
        buttons = [
            [InlineKeyboardButton(
                f"📰 {t['name']}",
                callback_data=f"quicksub:{t['id']}",
            )]
            for t, _score in matches[:3]
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
        "Use /subscribe to browse categories and search magazines.",
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
    matches = fuzzy_match_title(query_text, all_titles)

    if len(matches) == 1:
        title, score = matches[0]
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

    if len(matches) > 1:
        buttons = [
            [InlineKeyboardButton(
                f"📰 {t['name']}",
                callback_data=f"quickunsub:{t['id']}",
            )]
            for t, _score in matches[:3]
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


async def handle_quicksub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    title_id = int(parts[1])
    user_id = update.effective_user.id
    db_path = context.bot_data["config"].db_path
    
    all_titles = await get_all_titles(db_path)
    title = next((t for t in all_titles if t["id"] == title_id), None)
    title_name = title["name"] if title else f"Title #{title_id}"

    already = await is_subscribed(db_path, user_id, title_id)
    if already:
        await query.edit_message_text(
            f"ℹ️ You're already subscribed to <b>{title_name}</b>.",
            parse_mode="HTML",
        )
        return

    await subscribe(db_path, user_id, title_id)
    await query.edit_message_text(
        f"✅ Subscribed to <b>{title_name}</b>!\n"
        "📬 You'll receive it automatically.",
        parse_mode="HTML",
    )


async def handle_quickunsub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    title_id = int(parts[1])
    user_id = update.effective_user.id
    db_path = context.bot_data["config"].db_path

    all_titles = await get_all_titles(db_path)
    title = next((t for t in all_titles if t["id"] == title_id), None)
    title_name = title["name"] if title else f"Title #{title_id}"

    already = await is_subscribed(db_path, user_id, title_id)
    if not already:
        await query.edit_message_text(
            f"ℹ️ You're not subscribed to <b>{title_name}</b>.",
            parse_mode="HTML",
        )
        return

    await unsubscribe(db_path, user_id, title_id)
    await query.edit_message_text(
        f"🗑️ Unsubscribed from <b>{title_name}</b>.\n"
        "You won't receive this title anymore.",
        parse_mode="HTML",
    )
