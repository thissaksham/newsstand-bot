"""
General-purpose utility functions for the Newsstand Bot.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Sequence, TypeVar
from zoneinfo import ZoneInfo

from thefuzz import fuzz, process

T = TypeVar("T")


# ── fuzzy title search ──────────────────────────────────────────────

def fuzzy_match_title(
    query: str,
    titles: Sequence[dict],
    *,
    key: str = "name",
    limit: int = 5,
    score_cutoff: int = 50,
) -> list[tuple[dict, int]]:
    """Return the best fuzzy matches for *query* among *titles*.

    Parameters
    ----------
    query:
        The user's search string.
    titles:
        Sequence of dicts, each expected to have a *key* field.
    key:
        Dict key whose value is compared against *query*.
    limit:
        Maximum number of results.
    score_cutoff:
        Minimum score (0–100) to include in results.

    Returns
    -------
    list[tuple[dict, int]]
        ``(title_dict, score)`` pairs sorted best-first.
    """
    if not titles:
        return []

    # Build a name→dict lookup (handles duplicate names gracefully)
    name_map: dict[str, dict] = {t[key]: t for t in titles}
    names = list(name_map.keys())

    matches = process.extract(
        query,
        names,
        scorer=fuzz.token_sort_ratio,
        limit=limit,
    )

    return [
        (name_map[name], score)
        for name, score, *_ in matches
        if score >= score_cutoff
    ]


# ── date formatting ─────────────────────────────────────────────────

def format_date(d: date) -> str:
    """Format a date as ``'11 Jun 2026'``."""
    return d.strftime("%d %b %Y")


# ── tracker row (for /mystuff style views) ──────────────────────────

def format_tracker_row(
    title_name: str,
    delivered_days: int,
    total_days: int,
) -> str:
    """Build an emoji progress row like:

    ``📰 Times of India  ✅✅✅❌✅✅✅ (5/7)``
    """
    check = "✅"
    cross = "❌"
    missed = total_days - delivered_days
    bar = check * delivered_days + cross * missed
    return f"📰 {title_name}  {bar} ({delivered_days}/{total_days})"


# ── pagination ───────────────────────────────────────────────────────

def paginate_list(
    items: Sequence[T],
    page: int,
    per_page: int = 10,
) -> tuple[list[T], int]:
    """Slice *items* into pages.

    Parameters
    ----------
    items:
        Full list of items.
    page:
        1-indexed page number.
    per_page:
        Items per page.

    Returns
    -------
    tuple[list[T], int]
        ``(page_items, total_pages)``
    """
    total_pages = max(1, -(-len(items) // per_page))  # ceiling division
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    end = start + per_page
    return list(items[start:end]), total_pages


# ── Telegram MarkdownV2 escaping ────────────────────────────────────

_MARKDOWNV2_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def escape_markdown(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2.

    See https://core.telegram.org/bots/api#markdownv2-style
    """
    return _MARKDOWNV2_SPECIAL.sub(r"\\\1", text)


# ── file size formatting ────────────────────────────────────────────

def format_file_size(size_bytes: int | float) -> str:
    """Human-readable file size: ``format_file_size(2_500_000)`` → ``'2.4 MB'``."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1_048_576:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1_073_741_824:
        return f"{size_bytes / 1_048_576:.1f} MB"
    return f"{size_bytes / 1_073_741_824:.2f} GB"


# ── timezone-aware "today" ──────────────────────────────────────────

from datetime import timedelta

def get_today(timezone: str = "Asia/Kolkata") -> date:
    """Return today's date in the given timezone."""
    return datetime.now(ZoneInfo(timezone)).date()
