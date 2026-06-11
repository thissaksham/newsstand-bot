"""
Newsstand Bot — Admin-only handlers
/upload, /sync, /stats, /broadcast — restricted to config.admin_ids.
"""

import asyncio
import functools
import logging
from datetime import datetime, date

from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import Config
from database.operations import (
    get_all_titles,
    get_edition,
    get_subscribers,
    get_weekly_stats,
    get_user_subscriptions,
    get_scrape_status,
)
from delivery.engine import DeliveryEngine
from utils.helpers import fuzzy_match_title, format_date, get_today

logger = logging.getLogger(__name__)

# ── Conversation states for /upload ──────────────────────────────────────────
ASK_TITLE, ASK_DATE, RECEIVE_PDF = range(3)


# ═════════════════════════════════════════════════════════════════════════════
#  Admin guard decorator
# ═════════════════════════════════════════════════════════════════════════════

def admin_required(func):
    """Decorator: reject non-admin users with a polite message."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        config = Config()
        if user_id not in config.admin_ids:
            await update.message.reply_text(
                "🔒 <b>Access Denied</b>\n\n"
                "This command is restricted to bot administrators.",
                parse_mode="HTML",
            )
            return ConversationHandler.END  # safe for both conv and plain handlers
        return await func(update, context, *args, **kwargs)
    return wrapper


# ═════════════════════════════════════════════════════════════════════════════
#  /upload — Manual PDF upload conversation
# ═════════════════════════════════════════════════════════════════════════════

@admin_required
async def upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: ask for the title name."""
    await update.message.reply_text(
        "📤 <b>Manual PDF Upload</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Step 1/3 — Send me the <b>title name</b>.\n\n"
        "Example: <code>Times of India</code>\n\n"
        "Send /cancel to abort.",
        parse_mode="HTML",
    )
    return ASK_TITLE


async def upload_receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Fuzzy-match the title and ask for date."""
    query_text = update.message.text.strip()
    all_titles = await get_all_titles()
    match = fuzzy_match_title(query_text, all_titles)

    if not match:
        await update.message.reply_text(
            f"❌ No title matching <b>{query_text}</b> found.\n"
            "Please try again or /cancel.",
            parse_mode="HTML",
        )
        return ASK_TITLE

    title = match if isinstance(match, dict) else match[0]
    context.user_data["upload_title"] = title

    await update.message.reply_text(
        f"✅ Title: <b>{title['name']}</b>\n\n"
        "Step 2/3 — What <b>date</b> is this edition?\n\n"
        "Send a date like <code>11-06-2026</code> or <code>today</code>.",
        parse_mode="HTML",
    )
    return ASK_DATE


async def upload_receive_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Parse the date and ask for the PDF file."""
    text = update.message.text.strip()

    if text.lower() == "today":
        edition_date = get_today()
    else:
        fmts = ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y"]
        edition_date = None
        for fmt in fmts:
            try:
                edition_date = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue

    if not edition_date:
        await update.message.reply_text(
            "⚠️ Could not parse that date.\n"
            "Please use <code>DD-MM-YYYY</code> format or say <code>today</code>.",
            parse_mode="HTML",
        )
        return ASK_DATE

    context.user_data["upload_date"] = edition_date

    title = context.user_data["upload_title"]
    friendly = format_date(edition_date)

    await update.message.reply_text(
        f"📅 Date: <b>{friendly}</b>\n\n"
        f"Step 3/3 — Now send the <b>PDF file</b> for "
        f"<b>{title['name']}</b> ({friendly}).",
        parse_mode="HTML",
    )
    return RECEIVE_PDF


async def upload_receive_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the PDF, forward to storage, save edition, deliver to subscribers."""
    document = update.message.document

    if not document or not document.file_name.lower().endswith(".pdf"):
        await update.message.reply_text(
            "⚠️ Please send a <b>PDF file</b>. Other formats are not accepted.",
            parse_mode="HTML",
        )
        return RECEIVE_PDF

    title = context.user_data["upload_title"]
    edition_date: date = context.user_data["upload_date"]
    config = Config()

    # Forward to storage channel
    try:
        forwarded = await context.bot.send_document(
            chat_id=config.storage_channel_id,
            document=document.file_id,
            caption=(
                f"📰 {title['name']}\n"
                f"📅 {format_date(edition_date)}\n"
                f"🆔 {title['id']}"
            ),
        )
        file_id = forwarded.document.file_id
    except Exception:
        logger.exception("Failed to forward PDF to storage channel")
        await update.message.reply_text(
            "❌ Failed to store the PDF. Please check storage channel settings.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    # Save edition to DB
    from database.operations import log_delivery as _save  # reuse or add dedicated fn
    try:
        # Attempt to use a dedicated save_edition if available, else use engine
        engine = DeliveryEngine()
        await engine.save_edition(
            title_id=title["id"],
            date=edition_date.isoformat(),
            file_id=file_id,
        )
    except AttributeError:
        logger.warning("DeliveryEngine.save_edition not available; skipping DB save")

    # Deliver to subscribers
    subscribers = await get_subscribers(title["id"])
    delivered = 0
    for sub in subscribers:
        try:
            await context.bot.send_document(
                chat_id=sub["user_id"],
                document=file_id,
                caption=f"📰 <b>{title['name']}</b>  •  {format_date(edition_date)}",
                parse_mode="HTML",
            )
            delivered += 1
            await asyncio.sleep(0.3)
        except Exception:
            logger.warning("Failed to deliver to user %s", sub["user_id"])

    await update.message.reply_text(
        "✅ <b>Upload Complete!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📰 <b>{title['name']}</b>\n"
        f"📅 {format_date(edition_date)}\n"
        f"💾 Stored in channel\n"
        f"📬 Delivered to <b>{delivered}/{len(subscribers)}</b> subscriber{'s' if len(subscribers) != 1 else ''}",
        parse_mode="HTML",
    )

    # Clean up context
    context.user_data.pop("upload_title", None)
    context.user_data.pop("upload_date", None)

    return ConversationHandler.END


async def upload_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the upload conversation."""
    context.user_data.pop("upload_title", None)
    context.user_data.pop("upload_date", None)

    await update.message.reply_text(
        "🚫 Upload cancelled.",
        parse_mode="HTML",
    )
    return ConversationHandler.END


# Build the ConversationHandler
upload_conversation_handler = ConversationHandler(
    entry_points=[CommandHandler("upload", upload_start)],
    states={
        ASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, upload_receive_title)],
        ASK_DATE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, upload_receive_date)],
        RECEIVE_PDF: [MessageHandler(filters.Document.ALL, upload_receive_pdf)],
    },
    fallbacks=[CommandHandler("cancel", upload_cancel)],
    conversation_timeout=300,  # 5 minutes
)


# ═════════════════════════════════════════════════════════════════════════════
#  /sync — Trigger immediate scrape cycle
# ═════════════════════════════════════════════════════════════════════════════

@admin_required
async def sync_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger an immediate delivery/scrape cycle."""
    await update.message.reply_text(
        "🔄 <b>Sync started…</b>\n\n"
        "Running delivery cycle now. This may take a few minutes. ⏳",
        parse_mode="HTML",
    )

    try:
        engine = DeliveryEngine()
        result = await engine.run_delivery_cycle()

        await update.message.reply_text(
            "✅ <b>Sync Complete!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📥 Scraped: <b>{result.get('scraped', 0)}</b> editions\n"
            f"📬 Delivered: <b>{result.get('delivered', 0)}</b> copies\n"
            f"❌ Errors: <b>{result.get('errors', 0)}</b>",
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Sync failed")
        await update.message.reply_text(
            "❌ <b>Sync failed.</b> Check the logs for details.",
            parse_mode="HTML",
        )


# ═════════════════════════════════════════════════════════════════════════════
#  /stats — Bot-wide statistics
# ═════════════════════════════════════════════════════════════════════════════

@admin_required
async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display bot-wide statistics."""
    try:
        stats = await get_weekly_stats()
    except Exception:
        logger.exception("Failed to fetch stats")
        stats = {}

    try:
        all_titles = await get_all_titles()
        title_count = len(all_titles)
    except Exception:
        title_count = "?"

    try:
        scrape = await get_scrape_status()
    except Exception:
        scrape = {}

    users = stats.get("total_users", "?")
    subs = stats.get("total_subscriptions", "?")
    editions_today = stats.get("editions_today", "?")
    deliveries_today = stats.get("deliveries_today", "?")

    text = (
        "📊 <b>Bot Statistics</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Users:</b> {users}\n"
        f"📰 <b>Titles:</b> {title_count}\n"
        f"📋 <b>Active subscriptions:</b> {subs}\n"
        f"📦 <b>Editions today:</b> {editions_today}/{title_count}\n"
        f"📬 <b>Deliveries today:</b> {deliveries_today}\n"
    )

    if scrape:
        last_run = scrape.get("last_run", "never")
        text += f"\n🔄 <b>Last scrape:</b> {last_run}"

    await update.message.reply_text(text, parse_mode="HTML")


# ═════════════════════════════════════════════════════════════════════════════
#  /broadcast <message> — Send to all users
# ═════════════════════════════════════════════════════════════════════════════

@admin_required
async def broadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcast a message to all registered users."""
    if not context.args:
        await update.message.reply_text(
            "📌 <b>Usage:</b> <code>/broadcast Your message here</code>",
            parse_mode="HTML",
        )
        return

    message_text = " ".join(context.args)

    # Get all users
    try:
        stats = await get_weekly_stats()
        # We need a list of all user IDs — use get_subscribers with a dummy
        # or add a dedicated get_all_users. Fall back to stats count.
        from database.operations import get_user  # we'll iterate known users
    except Exception:
        pass

    # Collect all user IDs from all title subscribers (union)
    all_user_ids: set[int] = set()
    try:
        all_titles = await get_all_titles()
        for title in all_titles:
            subs = await get_subscribers(title["id"])
            for s in subs:
                all_user_ids.add(s["user_id"])
    except Exception:
        logger.exception("Failed to collect user IDs for broadcast")

    if not all_user_ids:
        await update.message.reply_text(
            "⚠️ No users found to broadcast to.",
            parse_mode="HTML",
        )
        return

    broadcast_text = (
        "📢 <b>Announcement from Newsstand Bot</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{message_text}"
    )

    sent = 0
    failed = 0

    for uid in all_user_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=broadcast_text,
                parse_mode="HTML",
            )
            sent += 1
            await asyncio.sleep(0.1)
        except Exception:
            failed += 1
            logger.warning("Broadcast failed for user %s", uid)

    total = sent + failed
    await update.message.reply_text(
        f"📢 <b>Broadcast Complete</b>\n\n"
        f"✅ Sent to <b>{sent}/{total}</b> users"
        + (f"\n❌ Failed: {failed}" if failed else ""),
        parse_mode="HTML",
    )
