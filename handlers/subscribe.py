"""
Newsstand Bot — /subscribe, /sub, /unsub handlers
Interactive category → title browser with inline keyboards.
Supports newspaper languages and downmagaz.net magazine searches.
"""

import asyncio
import datetime
import logging
import re
import time
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
    get_titles_by_category,
    get_all_titles,
    subscribe,
    unsubscribe,
    is_subscribed,
    get_user_subscriptions,
    register_user,
    get_title_by_slug,
    add_title,
    search_titles,
    _get_client,
    log_delivery,
    has_been_delivered,
)
from utils.helpers import (
    format_date_long, html_escape, magazine_date_label,
    download_url_to_bytes, is_url, pdf_buffer,
)
from scrapers.downmagaz_net import (
    search_magazines,
    scrape_magazine_tag,
    get_download_links,
    get_magazine_tag_and_version,
    matches_version,
)

logger = logging.getLogger(__name__)

TITLES_PER_PAGE = 8

# Strong references to fire-and-forget background tasks. asyncio only keeps weak
# references to tasks, so without this set a task can be garbage-collected
# mid-run (which silently killed on-subscribe scrapes before).
_BG_TASKS: set[asyncio.Task] = set()

# Shared cloud IPs (e.g. Render) get rate-limited by indiags.com, so throttle
# manual premium scrapes to one per title every 10 minutes.
_PREMIUM_SCRAPE_COOLDOWN_SECONDS = 600
_LAST_PREMIUM_SCRAPE: dict[int, float] = {}


def _track_task(task: asyncio.Task) -> None:
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


async def _reply(query, update, bot, user_id: int, text: str) -> None:
    """Send/replace a status message via whichever channel we were called from."""
    if query:
        await query.edit_message_text(text, parse_mode="HTML", disable_web_page_preview=True)
    elif update:
        await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)
    else:
        await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML", disable_web_page_preview=True)


def _magazine_links_html(links: list[tuple[str, str]]) -> str:
    return "".join(
        f'• <a href="{html_escape(href)}">Download via {html_escape(domain)}</a>\n'
        for domain, href in links
    )


def _download_link_label(url: str) -> str:
    """Return a human-friendly label for a source download URL."""
    url_l = url.lower()
    if "drive.google.com" in url_l or "google.com" in url_l:
        return "Google Drive"
    if "indiags.com" in url_l:
        return "indiags.com"
    return "source"

# ── Conversation States ──────────────────────────────────────────────────────
SELECT_CATEGORY, AWAITING_MAGAZINE_NAME = range(2)


async def _send_premium_pdf_to_user(
    bot, user_id: int, title_name: str, edition_date: datetime.date,
    file_id: str, download_url: str | None,
) -> bool:
    """Send a premium title's PDF to one user.

    If ``file_id`` is a Telegram document file_id it is forwarded; otherwise the
    short-lived source URL is downloaded and uploaded as a document.
    """
    safe_name = html_escape(title_name)
    caption = (
        f"📰 <b>{safe_name}</b> — {format_date_long(edition_date)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Your edition is ready. 📄"
    )

    if file_id and not is_url(file_id):
        try:
            await bot.send_document(
                chat_id=user_id, document=file_id, caption=caption, parse_mode="HTML"
            )
            return True
        except Exception as e:
            logger.error("[%s] Failed to forward premium PDF to %s: %s", title_name, user_id, e)
            return False

    url = download_url or file_id
    if not url:
        return False

    pdf_bytes = await download_url_to_bytes(url)
    if not pdf_bytes:
        logger.error("[%s] Failed to download premium PDF for user %s", title_name, user_id)
        return False

    if len(pdf_bytes) > 20 * 1024 * 1024:
        try:
            await bot.send_message(
                chat_id=user_id, parse_mode="HTML",
                text=f"📰 <b>{safe_name}</b> — PDF is too large to send via Telegram ({len(pdf_bytes)//1024//1024} MB).",
            )
        except Exception:
            pass
        return False

    try:
        await bot.send_document(
            chat_id=user_id,
            document=pdf_buffer(pdf_bytes),
            caption=caption,
            parse_mode="HTML",
            filename=f"{title_name.replace(' ', '_')}_{edition_date.isoformat()}.pdf",
        )
        return True
    except Exception as e:
        logger.error("[%s] Failed to send premium PDF to %s: %s", title_name, user_id, e)
        return False


async def _deliver_stored_edition_to_user(bot, user_id: int, title_name: str, category: str, edition: dict) -> bool:
    """Deliver one already-stored edition's download link(s) or PDF to a single
    user and log the delivery. Assumes the caller checked it isn't a dup.

    Returns True only if the edition was actually sent — never logs a delivery
    for a message that didn't go out, so catch-up can retry later."""
    edition_id = edition["id"]
    edition_date = datetime.date.fromisoformat(edition["date"])
    file_id = edition.get("file_id") or ""
    safe_name = html_escape(title_name)

    if category == "Magazine":
        links = []
        for post_url in (p for p in file_id.split(",") if p):
            links.extend(await get_download_links(post_url))
        if not links:
            return False
        await bot.send_message(
            chat_id=user_id, parse_mode="HTML", disable_web_page_preview=True,
            text=(
                f"📖 <b>{safe_name}</b> — {magazine_date_label(title_name, edition_date)}\n"
                f"Latest available edition:\n"
                f"{_magazine_links_html(links)}"
            ),
        )
    elif category == "The Hindu/Indian Express":
        if not await _send_premium_pdf_to_user(
            bot, user_id, title_name, edition_date,
            file_id, edition.get("download_url"),
        ):
            return False
    else:
        link = file_id.split(",")[0]
        if not link.startswith(("http://", "https://")):
            return False
        await bot.send_message(
            chat_id=user_id, parse_mode="HTML", disable_web_page_preview=True,
            text=(
                f"📰 <b>{safe_name}</b> — {format_date_long(edition_date)}\n"
                f"Latest available edition:\n"
                f'<a href="{html_escape(link)}">⬇️ Download ({html_escape(_download_link_label(link))})</a>'
            ),
        )

    await log_delivery("", user_id, edition_id, "success")
    return True



async def handle_getlatest_callback(update, context) -> None:
    """'📥 Get latest' button in /subscriptions — re-send the latest stored
    edition of a subscribed title."""
    query = update.callback_query
    await query.answer("Fetching… ⏳")
    user_id = update.effective_user.id
    title_id = int(query.data.split(":", 1)[1])

    db = await _get_client()
    t = await db.table("titles").select("name, category, slug").eq("id", title_id).execute()
    if not t.data:
        await context.bot.send_message(user_id, "⚠️ That title no longer exists.")
        return
    name = t.data[0]["name"]
    category = t.data[0].get("category", "Newspaper")
    slug = t.data[0].get("slug", "")

    # Premium titles must be re-scraped on /getlatest because the short-lived
    # /go/ links expire and the stored Telegram file_id may point to yesterday's
    # paper that was mislabelled with today's date.
    if category == "The Hindu/Indian Express":
        now = time.time()
        last_scraped = _LAST_PREMIUM_SCRAPE.get(title_id, 0)
        if now - last_scraped < _PREMIUM_SCRAPE_COOLDOWN_SECONDS:
            remaining = int(_PREMIUM_SCRAPE_COOLDOWN_SECONDS - (now - last_scraped))
            await context.bot.send_message(
                user_id,
                f"⏳ <b>{html_escape(name)}</b> was checked recently. "
                f"Please wait {remaining // 60}m {remaining % 60}s to avoid rate limits.",
                parse_mode="HTML",
            )
            return

        _LAST_PREMIUM_SCRAPE[title_id] = now
        await context.bot.send_message(
            user_id,
            f"⏳ Fetching the latest <b>{html_escape(name)}</b>…",
            parse_mode="HTML",
        )
        _track_task(asyncio.create_task(
            _scrape_and_deliver_one(context.bot, slug, title_id, user_id, category, name)
        ))
        return

    ed = await db.table("editions").select("*").eq("title_id", title_id)\
        .eq("status", "delivered").order("date", desc=True).limit(1).execute()
    if not ed.data:
        await context.bot.send_message(
            user_id,
            f"📭 No edition of <b>{html_escape(name)}</b> is available yet — "
            f"you'll get it automatically as soon as one is published.",
            parse_mode="HTML",
        )
        return

    if not await _deliver_stored_edition_to_user(context.bot, user_id, name, category, ed.data[0]):
        await context.bot.send_message(
            user_id,
            f"📭 No download links are available yet for <b>{html_escape(name)}</b> — try again later.",
            parse_mode="HTML",
        )


async def _scrape_and_deliver_one(bot, slug: str, title_id: int, user_id: int, category: str, title_name: str) -> None:
    """On-demand: scrape a single just-subscribed title in-process, then deliver
    the latest edition to the user (unless the cycle already delivered it)."""
    try:
        from run_scrapers import run_scrape_cycle
        await run_scrape_cycle(bot, target_slug=slug)
    except Exception as ex:
        logger.error("[BG Scrape %s] cycle failed: %s", slug, ex)

    try:
        db = await _get_client()
        resp = await db.table("editions").select("*").eq("title_id", title_id)\
            .eq("status", "delivered").order("date", desc=True).limit(1).execute()

        if resp.data:
            edition = resp.data[0]
            if await has_been_delivered("", user_id, edition["id"]):
                return  # the scrape cycle already delivered it
            if await _deliver_stored_edition_to_user(bot, user_id, title_name, category, edition):
                return

        # Nothing stored, or stored but not sendable yet (e.g. magazine post with
        # no mirror links) — catch-up will deliver it once it's available.
        try:
            await bot.send_message(
                chat_id=user_id, parse_mode="HTML",
                text=(
                    f"😔 We couldn't fetch <b>{html_escape(title_name)}</b> right now. "
                    f"We'll keep checking and send it the moment it's available."
                ),
            )
        except Exception:
            pass
    except Exception as ex:
        logger.error("[BG Scrape %s] post-scrape delivery failed: %s", slug, ex)


async def deliver_latest_editions_on_subscribe(db_path: str, user_id: int, bot, title_id: int = None, query=None, update=None) -> None:
    """Deliver the latest available edition of newly-subscribed titles to the user.

    If a title has no edition stored yet, kick off an in-process scrape for just
    that title and deliver once it lands.
    """
    try:
        db = await _get_client()

        subs = await get_user_subscriptions(db_path, user_id)
        if not subs:
            return
        if title_id:
            subs = [s for s in subs if s["id"] == title_id]

        for sub in subs:
            tid = sub["id"]
            title_name = sub["name"]
            slug = sub["slug"]
            category = sub.get("category", "Newspaper")
            safe_name = html_escape(title_name)

            confirm_text = (
                f"✅ Subscribed to <b>{safe_name}</b>!\n"
                f"Whenever a new edition comes, we'll send it to you automatically! 🚀"
            )

            editions_resp = await db.table("editions").select("*").eq("title_id", tid)\
                .eq("status", "delivered").order("date", desc=True).limit(1).execute()

            if editions_resp.data:
                latest_edition = editions_resp.data[0]
                edition_id = latest_edition["id"]
                edition_date = datetime.date.fromisoformat(latest_edition["date"])
                file_id = latest_edition.get("file_id") or ""
                already_delivered = await has_been_delivered(db_path, user_id, edition_id)

                if category == "Magazine":
                    links = []
                    for post_url in (p for p in file_id.split(",") if p):
                        links.extend(await get_download_links(post_url))
                    if links:
                        msg_text = (
                            f"{confirm_text}\n\n"
                            f"📖 <b>{safe_name}</b> — {magazine_date_label(title_name, edition_date)}\n"
                            f"Latest available edition:\n"
                            f"{_magazine_links_html(links)}"
                        )
                    else:
                        msg_text = confirm_text
                    await _reply(query, update, bot, user_id, msg_text)
                    # Only mark delivered if the links actually went out —
                    # otherwise catch-up retries once mirrors appear.
                    if links and not already_delivered:
                        await log_delivery(db_path, user_id, edition_id, "success")
                elif category == "The Hindu/Indian Express":
                    if not already_delivered:
                        await _reply(query, update, bot, user_id, confirm_text)
                        ok = await _send_premium_pdf_to_user(
                            bot, user_id, title_name, edition_date,
                            file_id, latest_edition.get("download_url"),
                        )
                        if ok:
                            await log_delivery(db_path, user_id, edition_id, "success")
                    else:
                        await _reply(query, update, bot, user_id, confirm_text)
                else:
                    if not already_delivered:
                        link = file_id.split(",")[0]
                        msg_text = confirm_text
                        if link.startswith(("http://", "https://")):
                            msg_text += (
                                f"\n\n📰 <b>Latest available edition ({format_date_long(edition_date)}):</b>\n"
                                f'<a href="{html_escape(link)}">⬇️ Download ({html_escape(_download_link_label(link))})</a>'
                            )
                        await _reply(query, update, bot, user_id, msg_text)
                        await log_delivery(db_path, user_id, edition_id, "success")
                    else:
                        await _reply(query, update, bot, user_id, confirm_text)
            else:
                # No edition stored yet — scrape this one title in-process and
                # deliver when it lands (no fragile subprocess, no lost task).
                await _reply(
                    query, update, bot, user_id,
                    f"✅ Subscribed to <b>{safe_name}</b>!\n"
                    f"Whenever a new edition comes, we'll send it to you automatically! 🚀\n\n"
                    f"⏳ <b>Scraping in progress...</b>\n"
                    f"We are fetching the latest edition from the web. It will be delivered here shortly!",
                )
                _track_task(asyncio.create_task(
                    _scrape_and_deliver_one(bot, slug, tid, user_id, category, title_name)
                ))

    except Exception as e:
        logger.error("Error in deliver_latest_editions_on_subscribe: %s", e)


# ═════════════════════════════════════════════════════════════════════════════
#  /subscribe — Interactive category picker
# ═════════════════════════════════════════════════════════════════════════════

async def subscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show category/language picker as inline keyboard. Works whether entered by
    the /subscribe command or the 📰 Subscribe button on /start."""
    if update.callback_query:
        await update.callback_query.answer()
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
            f"🇮🇳 Indian {lang.title()} Dailies",
            callback_data=f"lang:{lang}",
        )])

    # 2. Premium English dailies category (The Hindu / Indian Express)
    buttons.append([InlineKeyboardButton(
        "📰 The Hindu / Indian Express",
        callback_data="cat:The Hindu/Indian Express",
    )])

    # 3. Magazines category
    buttons.append([InlineKeyboardButton(
        "🌍 International News & Magazines",
        callback_data="cat:magazine",
    )])

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "📰 <b>Subscribe — Choose a Category</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Pick a category or language to browse available titles:"
        ),
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
                f"🇮🇳 Indian {lang.title()} Dailies",
                callback_data=f"lang:{lang}",
            )])
        buttons.append([InlineKeyboardButton(
            "📰 The Hindu / Indian Express",
            callback_data="cat:The Hindu/Indian Express",
        )])
        buttons.append([InlineKeyboardButton(
            "🌍 International News & Magazines",
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
    # Keep only regular newspapers here; premium categories have their own browser.
    titles = [t for t in titles if t.get("category", "Newspaper") == "Newspaper"]

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
        f"🇮🇳 <b>Indian {language.title()} Dailies</b>  "
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

    subscribing = not await is_subscribed(db_path, user_id, title_id)
    if subscribing:
        await subscribe(db_path, user_id, title_id)
    else:
        await unsubscribe(db_path, user_id, title_id)

    await _show_titles_page(query, user_id, language, page, db_path=db_path)

    # Deliver only the title just subscribed to; DONE should just exit.
    if subscribing:
        await deliver_latest_editions_on_subscribe(db_path, user_id, context.bot, title_id=title_id)

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


# ── Callback: Category selected (Magazines or The Hindu/Indian Express) ──────

_CATEGORY_LABELS = {
    "The Hindu/Indian Express": "📰 The Hindu / Indian Express",
}

async def handle_cat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    category = query.data.split(":", 1)[1]

    if category == "magazine":
        await query.edit_message_text(
            "🌍 <b>International News &amp; Magazines</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Type the name of an international newspaper or magazine to search for "
            "(e.g. <i>The Economist</i>, <i>The Washington Post</i>):",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="lang:__back__")
            ]])
        )
        return AWAITING_MAGAZINE_NAME

    # Premium category browser (The Hindu / Indian Express)
    db_path = context.bot_data["config"].db_path
    await _show_category_titles_page(query, update.effective_user.id, category, page=0, db_path=db_path)
    return SELECT_CATEGORY


async def handle_category_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    parts = query.data.split(":")
    category = parts[1]
    page = int(parts[2])
    db_path = context.bot_data["config"].db_path
    await _show_category_titles_page(query, update.effective_user.id, category, page, db_path=db_path)
    return SELECT_CATEGORY


async def _show_category_titles_page(query, user_id: int, category: str, page: int, db_path: str) -> None:
    """Build title list for a non-language category with subscription toggles."""
    titles = await get_titles_by_category(db_path, category)

    if not titles:
        await query.edit_message_text(
            f"📭 No titles available for <b>{html_escape(category)}</b>.",
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
                callback_data=f"cattoggle:{t['id']}:{category}:{page}",
            )
        ])

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"catpage:{category}:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"catpage:{category}:{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([
        InlineKeyboardButton("🔙 Categories", callback_data="lang:__back__"),
        InlineKeyboardButton("✅ Done", callback_data="done"),
    ])

    label = _CATEGORY_LABELS.get(category, html_escape(category))
    header = (
        f"{label}\n"
        f"<i>(page {page + 1}/{total_pages})</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Tap a title to subscribe / unsubscribe:"
    )

    await query.edit_message_text(
        header,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_category_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    db_path = context.bot_data["config"].db_path
    parts = query.data.split(":")
    title_id = int(parts[1])
    category = parts[2]
    page = int(parts[3])

    user = update.effective_user
    await register_user(
        db_path=db_path,
        user_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )

    subscribing = not await is_subscribed(db_path, user_id, title_id)
    if subscribing:
        await subscribe(db_path, user_id, title_id)
    else:
        await unsubscribe(db_path, user_id, title_id)

    await _show_category_titles_page(query, user_id, category, page, db_path=db_path)

    # Deliver only the title just subscribed to; DONE should just exit.
    if subscribing:
        await deliver_latest_editions_on_subscribe(db_path, user_id, context.bot, title_id=title_id)

    return SELECT_CATEGORY


# ── Message: Text search query received ──────────────────────────────────────

async def handle_magazine_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query_text = update.message.text.strip()
    safe_query = html_escape(query_text)
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
            f"❌ No matching magazines found for <b>{safe_query}</b>.\n"
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
        f"🔍 <b>Search Results for '{safe_query}'</b>:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select the international newspaper or magazine you want to subscribe to:",
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
        # Deliver latest edition immediately (will scrape if not in DB) and send combined confirmation message
        await deliver_latest_editions_on_subscribe(db_path, user_id, context.bot, title_id=title_id, query=query)
        
    return ConversationHandler.END


# ═════════════════════════════════════════════════════════════════════════════
#  Conversation Handler Definition
# ═════════════════════════════════════════════════════════════════════════════

subscribe_conversation_handler = ConversationHandler(
    entry_points=[
        CommandHandler("subscribe", subscribe_handler),
        CallbackQueryHandler(subscribe_handler, pattern="^start_subscribe$"),
    ],
    states={
        SELECT_CATEGORY: [
            CallbackQueryHandler(handle_lang_callback, pattern="^lang:"),
            CallbackQueryHandler(handle_cat_callback, pattern="^cat:"),
            CallbackQueryHandler(handle_toggle_callback, pattern="^toggle:"),
            CallbackQueryHandler(handle_page_callback, pattern="^page:"),
            CallbackQueryHandler(handle_category_page_callback, pattern="^catpage:"),
            CallbackQueryHandler(handle_category_toggle_callback, pattern="^cattoggle:"),
            CallbackQueryHandler(handle_done_callback, pattern="^done$"),
        ],
        AWAITING_MAGAZINE_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_magazine_search),
            CallbackQueryHandler(handle_lang_callback, pattern="^lang:"),
            CallbackQueryHandler(handle_submag_callback, pattern="^submag:"),
        ],
    },
    fallbacks=[
        CallbackQueryHandler(handle_done_callback, pattern="^done$"),
    ],
)
