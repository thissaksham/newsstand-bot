"""
Newsstand Bot — indiags.com scraper for The Hindu / Indian Express.

The listing page at https://www.indiags.com/epaper-pdf-download contains a
grid of newspaper cards (``div.ep-grid``). Each card has a footer link like
``https://www.indiags.com/epaper/books/<id>``. Visiting the corresponding
``/epaper/open/<id>`` page and waiting for the unlock timer reveals a final
download link inside ``div#pdfUnlockBanner`` (a ``/go/<token>`` URL). That
URL is what we store and send to subscribers.

This module follows the same ``find_download_link(source_url, dates_to_try)``
interface used by the other newspaper scrapers so the scheduler, /get and the
web UI can use it unchanged. The title name is passed as a keyword argument by
``scrapers.find_newspaper_link``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import urllib.request
from datetime import date

from bs4 import BeautifulSoup

from utils.helpers import get_today

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.indiags.com"
_LISTING_URL = "https://www.indiags.com/epaper-pdf-download"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.google.com/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Cache the listing-page parse for a few minutes so a multi-title scrape does not
# refetch the same grid repeatedly. Tuple of (expires_at, papers).
_listing_cache: tuple[float, list[dict[str, str]]] = (0.0, [])
_LISTING_TTL_SECONDS = 300


class IndiagsError(Exception):
    """Raised when the indiags.com flow cannot produce a download link."""


async def _fetch_html(url: str, timeout: float = 30.0) -> str:
    """Fetch a page synchronously (urllib) off the asyncio event loop."""
    req = urllib.request.Request(url, headers=HEADERS)

    def _fetch() -> str:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            encoding = resp.headers.get_content_charset("utf-8")
            return raw.decode(encoding, errors="replace")

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch)


def _normalise_name(name: str) -> str:
    """Strip extra whitespace and lowercase for fuzzy matching."""
    return re.sub(r"\s+", " ", name.strip()).lower()


def _extract_book_id(books_url: str) -> str | None:
    """Pull the numeric id out of ``/epaper/books/<id>``."""
    match = re.search(r"/epaper/books/(\d+)", books_url)
    return match.group(1) if match else None


def _find_go_link(html: str) -> str | None:
    """Search parsed HTML for the ``/go/<token>`` link inside #pdfUnlockBanner."""
    soup = BeautifulSoup(html, "html.parser")
    banner = soup.find("div", id="pdfUnlockBanner")
    if not banner:
        # Some layouts may put the link directly in the page without the banner
        # wrapper; accept any /go/ link as a fallback.
        banner = soup

    for a in banner.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("/go/"):
            return f"{_BASE_URL}{href}"
        if "/go/" in href and href.startswith("http"):
            return href

    # Also look for raw /go/ URLs in script or text content.
    for match in re.finditer(r"https?://[^\"'<>\s]*?/go/[A-Za-z0-9]+", str(banner)):
        return match.group(0)

    return None


async def _fetch_open_page_link(book_id: str) -> str:
    """Visit ``/epaper/open/<id>``, wait for the unlock timer, and return the
    ``/go/`` download URL.

    The site presents a countdown before the link is clickable. We mirror that
    by fetching the page, sleeping 20 seconds, then re-fetching to grab the
    revealed banner link. If the link is already present on the first fetch we
    return it immediately.
    """
    open_url = f"{_BASE_URL}/epaper/open/{book_id}"
    logger.debug("[indiags] Fetching open page %s", open_url)

    first_html = await _fetch_html(open_url)
    link = _find_go_link(first_html)
    if link:
        logger.debug("[indiags] Found go-link on first fetch: %s", link)
        return link

    logger.info("[indiags] No go-link yet for %s; waiting 20s for unlock...", book_id)
    await asyncio.sleep(20)

    second_html = await _fetch_html(open_url)
    link = _find_go_link(second_html)
    if link:
        logger.debug("[indiags] Found go-link after wait: %s", link)
        return link

    raise IndiagsError(f"No /go/ link found on {open_url} after waiting.")


async def _parse_listing_page(html: str) -> list[dict[str, str]]:
    """Return the list of newspapers from ``div.ep-grid``.

    Each item is ``{"name": str, "books_url": str, "book_id": str}``.
    """
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.find("div", class_="ep-grid")
    if not grid:
        # Gracefully fall back to the whole document if the grid class is not
        # found — the site may have changed layout slightly.
        grid = soup
        logger.warning("[indiags] div.ep-grid not found; scanning full page.")

    papers: list[dict[str, str]] = []
    seen_ids = set()

    # Each newspaper card may be a direct child or nested inside the grid.
    for card in grid.find_all("div", class_=True):
        # The card title is usually in an element whose text contains the paper
        # name. We look for a nearby ``.foot`` footer with the books link.
        foot = card.find("div", class_="foot")
        if not foot:
            continue

        a = foot.find("a", href=True)
        if not a:
            continue

        books_url = a["href"].strip()
        if books_url.startswith("/"):
            books_url = f"{_BASE_URL}{books_url}"

        book_id = _extract_book_id(books_url)
        if not book_id or book_id in seen_ids:
            continue

        # Try to extract the newspaper name. Prefer a dedicated title element,
        # otherwise use the card's own readable text.
        title_el = (
            card.find(class_="title")
            or card.find(class_="ep-title")
            or card.find("h3")
            or card.find("h4")
        )
        if title_el:
            name = title_el.get_text(strip=True)
        else:
            name = card.get_text(" ", strip=True)
            # Truncate to the first few words if the card text is noisy.
            name = " ".join(name.split()[:6])

        if not name:
            continue

        seen_ids.add(book_id)
        papers.append({"name": name, "books_url": books_url, "book_id": book_id})

    return papers


async def list_papers() -> list[dict[str, str]]:
    """Return the current set of newspapers on the indiags listing page.

    The result is cached for ``_LISTING_TTL_SECONDS`` to avoid redundant fetches
    when several configured titles are scraped in the same cycle.
    """
    global _listing_cache

    expires, papers = _listing_cache
    if time.time() < expires:
        return papers

    html = await _fetch_html(_LISTING_URL)
    papers = await _parse_listing_page(html)
    _listing_cache = (time.time() + _LISTING_TTL_SECONDS, papers)
    return papers


def _title_matches(card_name: str, target_name: str) -> bool:
    """Fuzzy-ish match between a card title and a configured title name."""
    card = _normalise_name(card_name)
    target = _normalise_name(target_name)

    if target in card or card in target:
        return True

    # Handle "The Hindu" matching "The Hindu" even if card says "The Hindu Delhi"
    target_words = set(target.split())
    card_words = set(card.split())
    if target_words and target_words <= card_words:
        return True

    return False


async def find_download_link(
    source_url: str,
    dates_to_try: list[date],
    *,
    title_name: str | None = None,
) -> tuple[date, str] | None:
    """Return ``(today, go_url)`` for the configured title.

    ``source_url`` is the title's configured source URL; for indiags titles it
    is the shared listing page. ``dates_to_try`` is ignored because the listing
    page always shows today's editions. ``title_name`` is required so we know
    which newspaper in the grid to pick.
    """
    if not title_name:
        logger.error("[indiags] title_name is required to match a newspaper card.")
        return None

    try:
        papers = await list_papers()
    except Exception as e:
        logger.error("[indiags] Failed to fetch listing page: %s", e)
        return None

    if not papers:
        logger.warning("[indiags] No newspapers found on listing page.")
        return None

    for paper in papers:
        if _title_matches(paper["name"], title_name):
            try:
                link = await _fetch_open_page_link(paper["book_id"])
            except IndiagsError as e:
                logger.error("[indiags] %s", e)
                return None
            today = get_today()
            logger.info("[indiags] Found %s for %s -> %s", title_name, today, link)
            return today, link

    logger.warning(
        "[indiags] Title '%s' not found among %s",
        title_name,
        [p["name"] for p in papers],
    )
    return None


async def get_latest_download_link(
    source_url: str, title_name: str | None = None
) -> tuple[date, str] | None:
    """Convenience wrapper: latest (today's) link for a title."""
    return await find_download_link(source_url, [get_today()], title_name=title_name)
