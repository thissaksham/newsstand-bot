"""
Newsstand Bot — /tracker handler
Visual weekly delivery summary for subscribed titles.
"""

import logging
from datetime import timedelta

from telegram import Update
from telegram.ext import ContextTypes

from database.operations import get_user_subscriptions, log_delivery, get_edition
from utils.helpers import format_date, get_today

logger = logging.getLogger(__name__)

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ═════════════════════════════════════════════════════════════════════════════
#  /tracker — Weekly delivery summary
# ═════════════════════════════════════════════════════════════════════════════

async def tracker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show a visual grid of delivery status for the past 7 days."""
    user_id = update.effective_user.id
    subs = await get_user_subscriptions(user_id)

    if not subs:
        await update.message.reply_text(
            "📭 <b>No subscriptions yet.</b>\n\n"
            "Subscribe to titles first with /subscribe, then come back to track delivery!",
            parse_mode="HTML",
        )
        return

    today = get_today()
    week_start = today - timedelta(days=6)
    dates = [week_start + timedelta(days=i) for i in range(7)]

    date_header = f"{format_date(week_start)} – {format_date(today)}"

    lines: list[str] = [
        f"📊 <b>Your Weekly Tracker</b>",
        f"📅 {date_header}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    total_delivered = 0
    total_expected = 0

    # Column headers (day abbreviations)
    day_abbrevs = "".join(f"{d.strftime('%a')[:2]} " for d in dates)
    lines.append(f"<code>{'Title':<20} {day_abbrevs} Score</code>")
    lines.append(f"<code>{'─' * 20} {'── ' * 7}─────</code>")

    for sub in subs:
        title_name = sub["name"]
        # Truncate long names
        display_name = title_name[:19] if len(title_name) > 19 else title_name

        day_marks: list[str] = []
        delivered_count = 0

        for d in dates:
            edition = await get_edition(sub["id"], d.isoformat())
            if edition and edition.get("file_id"):
                day_marks.append("✅")
                delivered_count += 1
            else:
                day_marks.append("❌")

        total_delivered += delivered_count
        total_expected += 7

        marks_str = " ".join(day_marks)
        score = f"{delivered_count}/7"

        lines.append(f"📰 {display_name}")
        lines.append(f"    {marks_str}  <b>{score}</b>")

    # Summary footer
    pct = round(total_delivered / total_expected * 100) if total_expected else 0
    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📈 <b>Total:</b> {total_delivered}/{total_expected} editions ({pct}%)",
    ])

    if pct == 100:
        lines.append("🎉 Perfect week! Every edition delivered.")
    elif pct >= 80:
        lines.append("👍 Great coverage this week!")
    elif pct >= 50:
        lines.append("⚠️ Some editions were missed.")
    else:
        lines.append("😕 Several editions were unavailable this week.")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
