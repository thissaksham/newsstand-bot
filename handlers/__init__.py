"""
Newsstand Bot — Handler Registration
Registers all command handlers, conversation handlers, and callback routers.
"""

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .start import start_handler, help_handler
from .subscribe import subscribe_handler, sub_handler, unsub_handler
from .subscriptions import subscriptions_handler
from .get import get_conversation_handler
from .callbacks import callback_router


def register_handlers(app: Application) -> None:
    """Register all bot handlers in the correct order."""

    # ── Conversation handlers (must be registered before generic callbacks) ──
    app.add_handler(get_conversation_handler)

    # ── Command handlers ─────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("subscribe", subscribe_handler))
    app.add_handler(CommandHandler("sub", sub_handler))
    app.add_handler(CommandHandler("unsub", unsub_handler))
    app.add_handler(CommandHandler("unsubscribe", unsub_handler))
    app.add_handler(CommandHandler("subscriptions", subscriptions_handler))

    # ── Callback query router (catch-all for inline keyboard presses) ────────
    app.add_handler(CallbackQueryHandler(callback_router))
