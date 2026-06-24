"""
General-purpose utility functions for the Newsstand Bot.
"""

from __future__ import annotations

import html as _html
import re
from datetime import date, datetime, timedelta
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


def format_date_long(d: date) -> str:
    """Format a date with weekday: ``'Wed, 25 Jun 2026'`` — used for newspaper
    editions, which are tied to a specific day."""
    return d.strftime("%a, %d %b %Y")


def magazine_date_label(title: str, edition_date: date) -> str:
    """Button label for a magazine issue.

    Dated issues (dailies/weeklies) get a full date — ``'24 Jun 2026'``. Monthly
    issues carry a month-start date, so they collapse to ``'Jun 2026'``. Since a
    *daily* can also land on the 1st, day-1 dates only collapse when the title
    has no explicit day component.
    """
    if edition_date.day != 1:
        return format_date(edition_date)
    months = r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
    has_explicit_day = bool(
        re.search(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", title)                         # 06.01.2026
        or re.search(rf"\b\d{{1,2}}\s+(?:{months})", title, re.IGNORECASE)            # "1 June"
        or re.search(rf"(?:{months})[a-z]*\.?\s+\d{{1,2}}\b", title, re.IGNORECASE)   # "June 1"
    )
    return format_date(edition_date) if has_explicit_day else edition_date.strftime("%b %Y")


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


def get_today(timezone: str = "Asia/Kolkata") -> date:
    """Return today's date in the given timezone."""
    return datetime.now(ZoneInfo(timezone)).date()


# ── HTML safety for Telegram parse_mode="HTML" ──────────────────────

def html_escape(text: object) -> str:
    """Escape arbitrary text so it is safe inside Telegram HTML messages.

    Telegram rejects a message outright (BadRequest: can't parse entities)
    if dynamic content contains an unescaped ``&``, ``<`` or ``>``. Magazine
    titles, scraped link domains and user search strings all flow into HTML
    messages, so every such value must pass through here first.
    """
    return _html.escape("" if text is None else str(text), quote=True)


# ── edition recency ─────────────────────────────────────────────────

def is_recent_edition(
    edition_date: date,
    today: date,
    category: str = "Newspaper",
    *,
    days: int = 3,
) -> bool:
    """Whether an edition is fresh enough to actively push to subscribers.

    Magazines often carry a month-start date (e.g. ``2026-06-01`` for the
    "June 2026" issue), so they count as recent for the whole calendar month.
    Newspapers (and everything else) use a short day window. This guards both
    the live magazine alert path and the catch-up safety net from spamming
    subscribers with historical back-issues.
    """
    if category == "Magazine" and (
        edition_date.year == today.year and edition_date.month == today.month
    ):
        return True
    return edition_date >= today - timedelta(days=days)
