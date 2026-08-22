"""
Newsstand Bot — indiags.com scraper for The Hindu / Indian Express.

The listing page at https://www.indiags.com/epaper-pdf-download contains a
grid of newspaper cards (``div.ep-grid``). Each card has a footer link like
``https://www.indiags.com/epaper/books/<id>``. Visiting the corresponding
``/epaper/open/<id>`` endpoint returns a 302 redirect whose Location header
contains a URL fragment ``#unlock=<url-encoded-go-link>&exp=<timestamp>``. The
page JavaScript decodes that hash and eventually injects it into
``div#pdfUnlockBanner`` as a ``/go/<token>`` URL.

We skip the countdown by reading the redirect Location (or final URL fragment)
directly and returning the ``/go/<token>`` URL, which is what we store and send
to subscribers.

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
import urllib.parse
import urllib.request
from datetime import date
from typing import Any

import httpx
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

_OPEN_PAGE_HEADERS = {
    **HEADERS,
    "Referer": "https://www.indiags.com/epaper-pdf-download",
}

# Cache the listing-page parse for a few minutes so a multi-title scrape does not
# refetch the same grid repeatedly. Tuple of (expires_at, papers).
_listing_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
_LISTING_TTL_SECONDS = 300


class IndiagsError(Exception):
    """Raised when the indiags.com flow cannot produce a download link."""


async def _fetch_html_and_url(url: str, timeout: float = 30.0) -> tuple[str, str]:
    """Fetch a page synchronously (urllib) and return (html, final_url).

    ``final_url`` includes the fragment if the server put one on the redirect
    Location and the redirect handler preserved it.
    """
    req = urllib.request.Request(url, headers=HEADERS)

    def _fetch() -> tuple[str, str]:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = resp.geturl()
            raw = resp.read()
            encoding = resp.headers.get_content_charset("utf-8")
            return raw.decode(encoding, errors="replace"), final_url

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch)


async def _fetch_html(url: str, timeout: float = 30.0) -> str:
    """Fetch a page synchronously (urllib) off the asyncio event loop."""
    html, _ = await _fetch_html_and_url(url, timeout=timeout)
    return html


def _normalise_name(name: str) -> str:
    """Strip extra whitespace and lowercase for fuzzy matching."""
    return re.sub(r"\s+", " ", name.strip()).lower()


_MONTH_ABBREVS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_card_date(text: str) -> date | None:
    """Parse a human-readable date from a listing card's meta element.

    Handles strings like ``22 Aug 2026``, ``22 Aug, 2026``, ``22-Aug-2026``.
    Returns ``None`` if no recognisable date is found.
    """
    if not text:
        return None
    match = re.search(
        r"(\d{1,2})\s*[-/.,]?\s*([A-Za-z]{3,})\s*[-/.,]?\s*(\d{4})",
        text,
    )
    if not match:
        return None
    day_str, month_str, year_str = match.groups()
    month_num = _MONTH_ABBREVS.get(month_str.lower()[:3])
    if not month_num:
        return None
    try:
        return date(int(year_str), month_num, int(day_str))
    except ValueError:
        return None


def _extract_book_id(books_url: str) -> str | None:
    """Pull the numeric id out of ``/epaper/books/<id>``."""
    match = re.search(r"/epaper/books/(\d+)", books_url)
    return match.group(1) if match else None


def _extract_go_link_from_url(url: str) -> str | None:
    """Extract the ``/go/<token>`` link from the URL hash.

    The server redirects the ``/epaper/open/<id>`` request to a URL like
    ``/some-page#unlock=<url-encoded-go-link>&exp=<unix-timestamp>``. The page
    JavaScript decodes that hash to reveal the real download URL. We can skip
    the timer by reading the fragment directly from the redirect Location
    header or from the final request URL.
    """
    fragment = urllib.parse.urlparse(url).fragment
    if not fragment or not fragment.startswith("unlock="):
        return None

    raw = fragment[len("unlock="):]
    exp_idx = raw.rfind("&exp=")
    if exp_idx != -1:
        raw = raw[:exp_idx]

    try:
        decoded = urllib.parse.unquote(raw)
    except Exception:
        return None

    decoded = decoded.strip()
    if not decoded:
        return None

    if decoded.startswith("/go/"):
        return f"{_BASE_URL}{decoded}"
    if decoded.startswith("http://") or decoded.startswith("https://"):
        return decoded

    # The decoded value might just be the token portion; construct full URL.
    if re.match(r"^[A-Za-z0-9_-]+$", decoded):
        return f"{_BASE_URL}/go/{decoded}"

    return None


def _find_go_link(html: str) -> str | None:
    """Search parsed HTML for the ``/go/<token>`` link inside #pdfUnlockBanner.

    Also scans the whole document and embedded scripts as a fallback.
    """
    soup = BeautifulSoup(html, "html.parser")

    # 1. Prefer the banner div described by the user.
    banner = soup.find("div", id="pdfUnlockBanner")
    if banner:
        for a in banner.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith("/go/"):
                return f"{_BASE_URL}{href}"
            if "/go/" in href and href.startswith("http"):
                return href

    # 2. Fall back to any /go/ anchor anywhere on the page.
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("/go/"):
            return f"{_BASE_URL}{href}"
        if "/go/" in href and href.startswith("http"):
            return href

    # 3. Look for raw /go/ URLs in script or text content.
    for match in re.finditer(r"https?://[^\"'<>\s]*?/go/[A-Za-z0-9]+", str(soup)):
        return match.group(0)

    return None


async def _fetch_open_page_with_session(book_id: str) -> str:
    """Fetch the unlock page using a single cookie-aware httpx session.

    The real download URL is hidden in the redirect fragment
    ``#unlock=<url-encoded-go-link>&exp=<ts>``. We try to capture that from the
    Location header first (fastest), then from the final URL if httpx preserved
    the fragment, and finally fall back to scanning the rendered HTML.
    """
    open_url = f"{_BASE_URL}/epaper/open/{book_id}"
    logger.info("[indiags] Opening unlock page with cookie session: %s", open_url)

    async with httpx.AsyncClient(
        headers=_OPEN_PAGE_HEADERS,
        follow_redirects=True,
        timeout=30.0,
    ) as client:
        # 1. Try without following redirects so we can read the Location header
        #    and its fragment intact.
        resp_redirect = await client.get(open_url, follow_redirects=False)
        location = resp_redirect.headers.get("location", "")
        if location:
            logger.info("[indiags] Redirect Location: %s", location)
            link = _extract_go_link_from_url(location)
            if link:
                logger.info("[indiags] Found go-link in redirect Location: %s", link)
                return link

        # 2. Follow redirects and check if the final URL retained the fragment.
        resp1 = await client.get(open_url)
        resp1.raise_for_status()
        final_url = str(resp1.url)
        logger.info("[indiags] First open-page fetch: HTTP %s, final_url=%s, cookies=%s", resp1.status_code, final_url, list(client.cookies.jar))

        link = _extract_go_link_from_url(final_url)
        if link:
            logger.info("[indiags] Found go-link in final URL: %s", link)
            return link

        first_html = resp1.text
        link = _find_go_link(first_html)
        if link:
            logger.info("[indiags] Found go-link in first-fetch HTML: %s", link)
            return link

        logger.info("[indiags] No go-link yet for %s; waiting 20s for unlock...", book_id)
        await asyncio.sleep(20)

        resp2 = await client.get(open_url)
        resp2.raise_for_status()
        second_html = resp2.text
        final_url2 = str(resp2.url)
        logger.info("[indiags] Second open-page fetch: HTTP %s, final_url=%s, cookies=%s", resp2.status_code, final_url2, list(client.cookies.jar))

        link = _extract_go_link_from_url(final_url2)
        if link:
            logger.info("[indiags] Found go-link in second final URL: %s", link)
            return link

        link = _find_go_link(second_html)
        if link:
            logger.info("[indiags] Found go-link after wait: %s", link)
            return link

    raise IndiagsError(f"No /go/ link found on {open_url} after waiting.")


async def _fetch_open_page_with_playwright(book_id: str) -> str:
    """Use Playwright (if installed) to load the unlock page in a real browser,
    wait for the countdown, and extract the ``/go/`` link from the DOM.

    This is the fallback of last resort when the site uses client-side
    JavaScript/AJAX to reveal the link after the timer.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise IndiagsError("Playwright is not installed.")

    open_url = f"{_BASE_URL}/epaper/open/{book_id}"
    logger.info("[indiags] Opening unlock page with Playwright: %s", open_url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=_OPEN_PAGE_HEADERS["User-Agent"],
            extra_http_headers={"Accept-Language": _OPEN_PAGE_HEADERS["Accept-Language"]},
        )
        page = await context.new_page()
        await page.goto(open_url, wait_until="networkidle")

        # Try immediately in case the link is already rendered.
        link = await _find_go_link_in_playwright_page(page)
        if link:
            logger.info("[indiags] Playwright found go-link immediately: %s", link)
            await browser.close()
            return link

        logger.info("[indiags] Playwright waiting 20s for unlock...")
        await asyncio.sleep(20)

        # Reload / wait a moment for any AJAX update, then re-scan.
        await page.reload(wait_until="networkidle")
        link = await _find_go_link_in_playwright_page(page)
        await browser.close()

        if link:
            logger.info("[indiags] Playwright found go-link after wait: %s", link)
            return link

    raise IndiagsError(f"Playwright could not find /go/ link on {open_url}.")


async def _find_go_link_in_playwright_page(page) -> str | None:
    """Extract a ``/go/`` href from the current Playwright page."""
    for sel in ["#pdfUnlockBanner a", "a[href*='/go/']", "a[href*='indiags.com/go/']"]:
        try:
            href = await page.get_attribute(sel, "href", timeout=2000)
        except Exception:
            continue
        if not href:
            continue
        href = href.strip()
        if href.startswith("/go/"):
            return f"{_BASE_URL}{href}"
        if "/go/" in href and href.startswith("http"):
            return href
    return None


async def _fetch_open_page_link(book_id: str) -> str:
    """Visit ``/epaper/open/<id>``, wait for the unlock timer, and return the
    ``/go/`` download URL.

    Order:
    1. Cookie-aware httpx session.
    2. Plain urllib (legacy, stateless).
    3. Playwright real-browser fallback (only if playwright is installed).
    """
    open_url = f"{_BASE_URL}/epaper/open/{book_id}"
    logger.info("[indiags] Fetching open page %s", open_url)

    try:
        return await _fetch_open_page_with_session(book_id)
    except Exception as e:
        logger.warning("[indiags] Cookie-session fetch failed (%s), falling back to plain urllib.", e)

    # urllib follow redirects via HTTPRedirectHandler preserves the Location
    # string on the response object only as the redirected url, which may or may
    # not keep the fragment. Try to extract from the returned URL first.
    first_html, first_url = await _fetch_html_and_url(open_url)
    link = _extract_go_link_from_url(first_url)
    if link:
        logger.info("[indiags] Found go-link in urllib final URL: %s", link)
        return link

    link = _find_go_link(first_html)
    if link:
        logger.info("[indiags] Found go-link on first urllib fetch: %s", link)
        return link

    logger.info("[indiags] No go-link yet for %s; waiting 20s for unlock...", book_id)
    await asyncio.sleep(20)

    second_html, second_url = await _fetch_html_and_url(open_url)
    link = _extract_go_link_from_url(second_url)
    if link:
        logger.info("[indiags] Found go-link in second urllib final URL: %s", link)
        return link

    link = _find_go_link(second_html)
    if link:
        logger.info("[indiags] Found go-link after urllib wait: %s", link)
        return link

    snippet = _snippet_around_banner(second_html)
    logger.warning("[indiags] Unlock page HTML snippet:\n%s", snippet)
    logger.warning("[indiags] Plain urllib fallback also failed; trying Playwright if available.")

    try:
        return await _fetch_open_page_with_playwright(book_id)
    except Exception as e:
        logger.warning("[indiags] Playwright fallback failed: %s", e)

    raise IndiagsError(f"No /go/ link found on {open_url} after all fallback methods.")


def _snippet_around_banner(html: str) -> str:
    """Return a small text snippet around #pdfUnlockBanner for debugging."""
    idx = html.lower().find("pdfunlockbanner")
    if idx == -1:
        return "(no #pdfUnlockBanner found in HTML)"
    start = max(0, idx - 200)
    end = min(len(html), idx + 400)
    return html[start:end]


async def _parse_listing_page(html: str) -> list[dict[str, Any]]:
    """Return the list of newspapers from ``div.ep-grid``.

    Each item is ``{"name": str, "books_url": str, "book_id": str,
    "edition_date": date | None}``. The edition date is read from the card's
    ``.meta`` element (e.g. "22 Aug 2026") so we label the paper correctly even
    when the site still shows yesterday's edition after midnight IST.
    """
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.find("div", class_="ep-grid")
    if not grid:
        # Gracefully fall back to the whole document if the grid class is not
        # found — the site may have changed layout slightly.
        grid = soup
        logger.warning("[indiags] div.ep-grid not found; scanning full page.")

    papers: list[dict[str, Any]] = []
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

        # Try to extract the newspaper name. Prefer the site's ``.ttl`` element,
        # otherwise fall back to other common title elements or the card text.
        title_el = (
            card.find(class_="ttl")
            or card.find(class_="title")
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

        # The card's .body > .meta carries the real edition date (e.g. "22 Aug 2026").
        edition_date: date | None = None
        body = card.find("div", class_="body")
        if body:
            meta = body.find(class_="meta")
            if meta:
                edition_date = _parse_card_date(meta.get_text(strip=True))

        seen_ids.add(book_id)
        papers.append({
            "name": name,
            "books_url": books_url,
            "book_id": book_id,
            "edition_date": edition_date,
        })

    return papers


async def list_papers() -> list[dict[str, Any]]:
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
    """Return ``(edition_date, go_url)`` for the configured title.

    ``source_url`` is the title's configured source URL; for indiags titles it
    is the shared listing page. ``dates_to_try`` is ignored because the listing
    page only carries the latest edition. ``title_name`` is required so we know
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

    logger.info("[indiags] Listing page returned %d newspaper card(s): %s", len(papers), [p["name"] for p in papers])

    if not papers:
        logger.warning("[indiags] No newspapers found on listing page.")
        return None

    for paper in papers:
        if _title_matches(paper["name"], title_name):
            edition_date = paper.get("edition_date") or get_today()
            logger.info(
                "[indiags] Matched '%s' to card '%s' (book_id=%s, edition_date=%s)",
                title_name, paper["name"], paper["book_id"], edition_date,
            )
            try:
                link = await _fetch_open_page_link(paper["book_id"])
            except IndiagsError as e:
                logger.error("[indiags] %s", e)
                return None
            logger.info("[indiags] Found %s for %s -> %s", title_name, edition_date, link)
            return edition_date, link

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
