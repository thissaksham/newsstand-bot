"""
Newsstand Bot — Handler Registration
Registers all command handlers, conversation handlers, and callback routers.
"""

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
)

from .start import start_handler, help_handler
from .subscribe import subscribe_conversation_handler, handle_getlatest_callback
from .subscriptions import subscriptions_handler
from .get import get_conversation_handler
from .callbacks import callback_router


def register_handlers(app: Application) -> None:
    """Register all bot handlers in the correct order."""

    # ── Conversation handlers (must be registered before generic callbacks) ──
    app.add_handler(get_conversation_handler)
    app.add_handler(subscribe_conversation_handler)

    # ── Command handlers ─────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("subscriptions", subscriptions_handler))

    # ── Specific callback buttons (these send a fresh message, so they must NOT
    #    go through the router's edit-on-error path). Registered before the
    #    catch-all so they take precedence.
    app.add_handler(CallbackQueryHandler(handle_getlatest_callback, pattern="^getlatest:"))
    app.add_handler(CallbackQueryHandler(subscriptions_handler, pattern="^start_mysubs$"))
    app.add_handler(CallbackQueryHandler(help_handler, pattern="^start_help$"))

    # ── Callback query router (catch-all for inline keyboard presses) ────────
    app.add_handler(CallbackQueryHandler(callback_router))
