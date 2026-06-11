"""
Newsstand Bot — /packs handler
Browse and subscribe to curated title packs.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.operations import (
    get_packs,
    get_pack_titles,
    subscribe_to_pack,
    is_subscribed,
    subscribe,
)

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
#  /packs — Browse curated packs
# ═════════════════════════════════════════════════════════════════════════════

async def packs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all available subscription packs."""
    packs = await get_packs()

    if not packs:
        await update.message.reply_text(
            "📭 No packs are configured yet. Check back later!",
            parse_mode="HTML",
        )
        return

    lines: list[str] = [
        "📦 <b>Subscription Packs</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "Subscribe to a curated bundle with one tap!",
        "",
    ]

    buttons: list[list[InlineKeyboardButton]] = []

    for pack in packs:
        pack_titles = await get_pack_titles(pack["id"])
        title_count = len(pack_titles)
        title_names = ", ".join(t["name"] for t in pack_titles[:4])
        if title_count > 4:
            title_names += f" +{title_count - 4} more"

        lines.append(
            f"📦 <b>{pack['name']}</b>  •  {title_count} title{'s' if title_count != 1 else ''}\n"
            f"   <i>{pack.get('description', '')}</i>\n"
            f"   📰 {title_names}\n"
        )

        buttons.append([
            InlineKeyboardButton(
                f"📦 Subscribe to {pack['name']}",
                callback_data=f"pack_sub:{pack['id']}",
            )
        ])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ═════════════════════════════════════════════════════════════════════════════
#  Callback: pack_sub:{pack_id}
# ═════════════════════════════════════════════════════════════════════════════

async def handle_pack_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Subscribe user to all titles in the selected pack."""
    query = update.callback_query
    user_id = update.effective_user.id
    pack_id = query.data.split(":", 1)[1]

    packs = await get_packs()
    pack = next((p for p in packs if str(p["id"]) == str(pack_id)), None)

    if not pack:
        await query.edit_message_text(
            "⚠️ Pack not found. It may have been removed.",
            parse_mode="HTML",
        )
        return

    pack_titles = await get_pack_titles(pack_id)

    already_count = 0
    new_count = 0

    for title in pack_titles:
        if await is_subscribed(user_id, title["id"]):
            already_count += 1
        else:
            await subscribe(user_id, title["id"])
            new_count += 1

    total = already_count + new_count

    if new_count == 0:
        status = (
            f"ℹ️ You're already subscribed to all <b>{total}</b> titles "
            f"in <b>{pack['name']}</b>."
        )
    elif already_count == 0:
        status = (
            f"✅ Subscribed to <b>{pack['name']}</b>!\n"
            f"📰 {new_count} title{'s' if new_count != 1 else ''} added."
        )
    else:
        status = (
            f"✅ Subscribed to <b>{pack['name']}</b>!\n"
            f"📰 {new_count} new title{'s' if new_count != 1 else ''} added "
            f"({already_count} already subscribed)."
        )

    status += "\n\n📬 Your papers will be delivered automatically each morning."

    await query.edit_message_text(status, parse_mode="HTML")
