"""
Newsstand Bot — /get and /today handlers
Retrieve specific editions interactively or all of today's subscribed papers.
"""

import asyncio
import logging
from datetime import datetime, date

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
)

from database.operations import (
    get_all_titles,
    get_edition,
    get_user_subscriptions,
)
from utils.helpers import format_date, get_today
import database.operations as db_ops

logger = logging.getLogger(__name__)

# ── Conversation States ──────────────────────────────────────────────────────
SELECT_LANGUAGE, SELECT_TITLE, SELECT_DATE = range(3)

# ═════════════════════════════════════════════════════════════════════════════
#  /get — Interactive Archive Fetcher
# ═════════════════════════════════════════════════════════════════════════════

async def get_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the /get conversation and ask for language."""
    keyboard = [
        [
            InlineKeyboardButton("🇺🇸 English", callback_data="get_lang_english"),
            InlineKeyboardButton("🇮🇳 Hindi", callback_data="get_lang_hindi"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "📰 <b>Newsstand Archives</b>\n\n"
        "Please select a language:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    return SELECT_LANGUAGE

async def get_language_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle language selection and show titles."""
    query = update.callback_query
    await query.answer()

    lang = query.data.split("_")[-1].lower() # english or hindi
    db_path = context.bot_data["config"].db_path
    all_titles = await get_all_titles(db_path)
    from database.operations import get_titles_with_editions
    available_title_ids = await get_titles_with_editions(db_path)
    
    # Filter titles by config language and availability
    config = context.bot_data.get("config")
    lang_titles = []
    if config:
        for t in config.titles:
            if getattr(t, "language", "").lower() == lang:
                # Find matching DB title
                db_t = next((dt for dt in all_titles if dt["slug"] == getattr(t, "slug", "")), None)
                if db_t and db_t["id"] in available_title_ids:
                    lang_titles.append(db_t)
                    
    if not lang_titles:
        await query.edit_message_text(
            f"No available {lang.capitalize()} titles found with recent editions.",
        )
        return ConversationHandler.END

    # Create buttons for titles (2 per row)
    keyboard = []
    row = []
    for t in lang_titles:
        row.append(InlineKeyboardButton(t["name"], callback_data=f"get_title_{t['id']}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton("🔙 Cancel", callback_data="get_cancel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        f"📰 <b>{lang.capitalize()} Newspapers</b>\n\n"
        "Please select a title:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    return SELECT_TITLE

DATES_PER_PAGE = 10

async def _show_dates_page(query, db_path: str, title_id: int, page: int) -> int:
    """Build date list with pagination."""
    all_titles = await get_all_titles(db_path)
    title = next((t for t in all_titles if t["id"] == title_id), None)
    
    if not title:
        await query.edit_message_text("❌ Title not found.")
        return ConversationHandler.END

    from database.operations import get_available_dates
    dates = await get_available_dates(db_path, title_id)
        
    if not dates:
        await query.edit_message_text(
            f"📭 No archive editions found for <b>{title['name']}</b> yet.\n"
            "Try again later!",
            parse_mode="HTML"
        )
        return ConversationHandler.END

    start = page * DATES_PER_PAGE
    end = start + DATES_PER_PAGE
    page_dates = dates[start:end]

    keyboard = []
    row_btns = []
    for d_str in page_dates:
        d_obj = datetime.strptime(d_str, "%Y-%m-%d").date()
        friendly_date = format_date(d_obj)
        
        row_btns.append(InlineKeyboardButton(friendly_date, callback_data=f"get_date_{title_id}_{d_str}"))
        if len(row_btns) == 2:
            keyboard.append(row_btns)
            row_btns = []
            
    if row_btns:
        keyboard.append(row_btns)
        
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Newer", callback_data=f"get_dpage_{title_id}_{page-1}"))
    if end < len(dates):
        nav_buttons.append(InlineKeyboardButton("Older ➡️", callback_data=f"get_dpage_{title_id}_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
        
    keyboard.append([InlineKeyboardButton("🔙 Cancel", callback_data="get_cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📅 <b>{title['name']}</b>\n\n"
        "Select an available date:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    return SELECT_DATE

async def get_title_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle title selection and show available dates."""
    query = update.callback_query
    await query.answer()
    title_id = int(query.data.split("_")[-1])
    db_path = context.bot_data["config"].db_path
    return await _show_dates_page(query, db_path, title_id, 0)

async def handle_dates_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle pagination for dates."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    title_id = int(parts[2])
    page = int(parts[3])
    db_path = context.bot_data["config"].db_path
    return await _show_dates_page(query, db_path, title_id, page)

async def get_date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle date selection and send the PDF."""
    query = update.callback_query
    await query.answer("Fetching your newspaper... ⏳")

    data_parts = query.data.split("_")
    title_id = int(data_parts[2])
    date_str = data_parts[3]
    
    db_path = context.bot_data["config"].db_path
    all_titles = await get_all_titles(db_path)
    title = next((t for t in all_titles if t["id"] == title_id), None)
    
    d_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
    edition = await get_edition(db_path, title_id, d_obj)
    
    if not edition or not edition.get("file_id"):
        await query.edit_message_text("❌ Sorry, this edition is no longer available.")
        return ConversationHandler.END
        
    friendly_date = format_date(d_obj)
    
    await query.edit_message_text(f"✅ Sending <b>{title['name']}</b> for {friendly_date}...", parse_mode="HTML")
    
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=edition["file_id"],
        caption=f"📰 <b>{title['name']}</b>  •  {friendly_date}",
        parse_mode="HTML",
    )
    return ConversationHandler.END

async def get_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the conversation."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Conversation cancelled.")
    return ConversationHandler.END

get_conversation_handler = ConversationHandler(
    entry_points=[CommandHandler("get", get_start)],
    states={
        SELECT_LANGUAGE: [
            CallbackQueryHandler(get_language_selected, pattern="^get_lang_")
        ],
        SELECT_TITLE: [
            CallbackQueryHandler(get_title_selected, pattern="^get_title_")
        ],
        SELECT_DATE: [
            CallbackQueryHandler(handle_dates_page_callback, pattern="^get_dpage_"),
            CallbackQueryHandler(get_date_selected, pattern="^get_date_")
        ],
    },
    fallbacks=[CallbackQueryHandler(get_cancel, pattern="^get_cancel$")],
    per_message=False,
)

# ═════════════════════════════════════════════════════════════════════════════
#  /today — Get all subscribed papers for today
# ═════════════════════════════════════════════════════════════════════════════

async def today_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send all of today's editions for the user's subscriptions."""
    user_id = update.effective_user.id
    db_path = context.bot_data["config"].db_path
    subs = await get_user_subscriptions(db_path, user_id)

    if not subs:
        await update.message.reply_text(
            "📭 <b>No subscriptions yet.</b>\n\n"
            "Use /subscribe to pick your papers first!",
            parse_mode="HTML",
        )
        return

    today = get_today()
    friendly_today = format_date(today)

    await update.message.reply_text(
        f"📰 <b>Today's Papers</b>  •  {friendly_today}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏳ Fetching {len(subs)} title{'s' if len(subs) != 1 else ''}…",
        parse_mode="HTML",
    )

    sent: list[str] = []
    pending: list[str] = []
    failed: list[str] = []

    for sub in subs:
        try:
            edition = await get_edition(db_path, sub["id"], today.isoformat())
            if edition and edition.get("file_id"):
                await update.message.reply_document(
                    document=edition["file_id"],
                    caption=f"📰 <b>{sub['name']}</b>  •  {friendly_today}",
                    parse_mode="HTML",
                )
                sent.append(sub["name"])
                # Small delay to avoid rate limits
                await asyncio.sleep(0.5)
            else:
                pending.append(sub["name"])
        except Exception:
            logger.exception("Failed to send %s to %s", sub["name"], user_id)
            failed.append(sub["name"])

    # Summary
    lines: list[str] = [
        "",
        "📊 <b>Summary</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if sent:
        lines.append(f"✅ <b>Delivered:</b> {len(sent)}")
        for name in sent:
            lines.append(f"   • {name}")

    if pending:
        lines.append(f"\n⏳ <b>Pending (not available yet):</b> {len(pending)}")
        for name in pending:
            lines.append(f"   • {name}")

    if failed:
        lines.append(f"\n❌ <b>Failed:</b> {len(failed)}")
        for name in failed:
            lines.append(f"   • {name}")

    if pending:
        lines.append("\n💡 <i>Pending titles will be delivered automatically when ready.</i>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
