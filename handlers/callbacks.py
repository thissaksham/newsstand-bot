"""
Newsstand Bot — Central Callback Query Router
Routes all inline-keyboard callback_data to the appropriate handler.
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from .subscribe import handle_lang_callback, handle_toggle_callback, handle_page_callback, handle_done_callback
from .subscriptions import handle_unsub_callback

logger = logging.getLogger(__name__)

# ── Routing table ────────────────────────────────────────────────────────────
_ROUTES: list[tuple[str, object]] = [
    ("lang:",     handle_lang_callback),
    ("toggle:",   handle_toggle_callback),
    ("page:",     handle_page_callback),
    ("unsub:",    handle_unsub_callback),
    ("done",      handle_done_callback),
]


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Central dispatcher for all CallbackQuery events."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()  # acknowledge the press immediately

    data: str = query.data

    for prefix, handler_fn in _ROUTES:
        if data.startswith(prefix) or data == prefix.rstrip(":"):
            try:
                await handler_fn(update, context)
            except Exception:
                logger.exception("Error handling callback %s", data)
                await query.edit_message_text(
                    "⚠️ Something went wrong. Please try again.",
                    parse_mode="HTML",
                )
            return

    logger.warning("Unhandled callback_data: %s", data)
