"""
Newsstand Bot — /lastupdated handler
Shows the last available edition date for each subscribed title.
"""

import logging
from datetime import timedelta

from telegram import Update
from telegram.ext import ContextTypes

from database.operations import get_user_subscriptions, get_latest_edition
from utils.helpers import format_date, get_today

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
#  /lastupdated — Last available date per subscribed title
# ═════════════════════════════════════════════════════════════════════════════

async def lastupdated_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """For each subscribed title, show the most recent edition date."""
    user_id = update.effective_user.id
    subs = await get_user_subscriptions(user_id)

    if not subs:
        await update.message.reply_text(
            "📭 <b>No subscriptions yet.</b>\n\n"
            "Use /subscribe to add titles, then check freshness here!",
            parse_mode="HTML",
        )
        return

    today = get_today()
    yesterday = today - timedelta(days=1)

    lines: list[str] = [
        "🕐 <b>Last Updated</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    fresh_count = 0
    stale_count = 0
    missing_count = 0

    for sub in subs:
        latest = await get_latest_edition(sub["id"])

        if not latest or not latest.get("date"):
            lines.append(f"📰 {sub['name']}  →  <i>No editions yet</i> 🔘")
            missing_count += 1
            continue

        # Parse the edition date
        try:
            from datetime import date as date_type, datetime
            if isinstance(latest["date"], str):
                edition_date = datetime.fromisoformat(latest["date"]).date()
            else:
                edition_date = latest["date"]
        except (ValueError, TypeError):
            lines.append(f"📰 {sub['name']}  →  <i>Unknown</i> ❓")
            missing_count += 1
            continue

        friendly = format_date(edition_date)

        if edition_date >= today:
            icon = "✅"
            fresh_count += 1
        elif edition_date >= yesterday:
            icon = "⚠️"
            stale_count += 1
        else:
            days_ago = (today - edition_date).days
            icon = f"🔴 ({days_ago}d ago)"
            stale_count += 1

        lines.append(f"📰 {sub['name']}  →  {friendly} {icon}")

    # Footer summary
    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ])

    total = len(subs)
    if fresh_count == total:
        lines.append("✅ <b>All titles are up to date!</b>")
    else:
        parts: list[str] = []
        if fresh_count:
            parts.append(f"✅ {fresh_count} fresh")
        if stale_count:
            parts.append(f"⚠️ {stale_count} stale")
        if missing_count:
            parts.append(f"🔘 {missing_count} no data")
        lines.append("  ".join(parts))

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
