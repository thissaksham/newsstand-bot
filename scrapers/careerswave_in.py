import re
import asyncio
import logging
import urllib.request
from bs4 import BeautifulSoup
from datetime import date, timedelta
from utils.helpers import get_today

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.google.com/",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# ── Date regex patterns ──────────────────────────────────────────────────────
# Old format: DD-MM-YYYY in table cells
DATE_RE  = re.compile(r'(\d{2}-\d{2}-\d{4})')

# New format: "DD Month YYYY" (e.g. "16 June 2026", "09 Jun 2026")
DATE_LONG_RE = re.compile(
    r'(\d{1,2})\s+'
    r'(January|February|March|April|May|June|July|August|September|October|November|December'
    r'|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+'
    r'(\d{4})',
    re.IGNORECASE,
)

HREF_RE  = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
BARE_RE  = re.compile(r'^\s*(https?://\S+)\s*$')
TD_RE    = re.compile(r'<td[^>]*>(.*?)</td>', re.IGNORECASE | re.DOTALL)

MONTH_MAP = {
    "january": 1, "jan": 1, "february": 2, "feb": 2,
    "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def fetch_page(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        encoding = resp.headers.get_content_charset("utf-8")
        return raw.decode(encoding, errors="replace")


def _clean_url(url: str) -> str:
    return url.strip().replace("&amp;", "&").replace("\\_", "_")


def _is_valid_download_url(url: str) -> bool:
    """Check if a URL looks like a valid download link (Google Drive, etc)."""
    if url.startswith("https://drive.google.com") or "savefrom" in url:
        return True
    if url.startswith("https://www.careerswave.in"):
        return False
    if "drive.google" in url or "/d/" in url:
        return True
    return not url.startswith("https://www.careerswave.in")


def parse_links(html: str) -> dict[str, str]:
    """Parse download links from the page, supporting BOTH formats:
    
    Format A (old): <td>DD-MM-YYYY</td><td><a href="...">Download</a></td>
    Format B (new): <p>DD Month YYYY:<a href="...">Download</a></p>
    
    Returns a dict mapping DD-MM-YYYY date strings to download URLs.
    """
    links: dict[str, str] = {}

    # ── Strategy 1: Old table format (DD-MM-YYYY in <td> cells) ──────────
    cells = TD_RE.findall(html)
    i = 0
    while i < len(cells) - 1:
        date_match = DATE_RE.search(cells[i])
        if date_match:
            date_str  = date_match.group(1)
            link_cell = cells[i + 1]

            url_match = HREF_RE.search(link_cell)
            if url_match:
                url = _clean_url(url_match.group(1))
                if _is_valid_download_url(url):
                    links[date_str] = url
            else:
                bare_match = BARE_RE.match(link_cell)
                if bare_match:
                    links[date_str] = _clean_url(bare_match.group(1))

            i += 2
        else:
            i += 1

    if links:
        return links

    # ── Strategy 2: New <p> / <a> format with "DD Month YYYY" dates ──────
    # Uses BeautifulSoup for robust parsing of the new layout
    soup = BeautifulSoup(html, "html.parser")

    # Find all <a> tags that point to Google Drive
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if "drive.google.com" not in href:
            continue

        # Look for the date in the parent element's text (usually a <p>)
        parent = a_tag.parent
        if not parent:
            continue
        parent_text = parent.get_text(strip=True)

        date_match = DATE_LONG_RE.search(parent_text)
        if date_match:
            day = int(date_match.group(1))
            month_str = date_match.group(2).lower()
            year = int(date_match.group(3))
            month = MONTH_MAP.get(month_str)
            if month:
                # Normalize to DD-MM-YYYY key format for consistency
                date_str = f"{day:02d}-{month:02d}-{year}"
                url = _clean_url(href)
                if date_str not in links:  # first match wins
                    links[date_str] = url

    if links:
        return links

    # ── Strategy 3: Fallback — scan ALL <a> tags with flexible date check ─
    # Some pages put dates in nearby text or sibling elements
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if "drive.google.com" not in href:
            continue

        # Check the text around the link: parent, previous sibling, etc.
        search_text = ""
        if a_tag.parent:
            search_text = a_tag.parent.get_text(" ", strip=True)
        if a_tag.previous_sibling and hasattr(a_tag.previous_sibling, "strip"):
            search_text = str(a_tag.previous_sibling) + " " + search_text

        # Try DD-MM-YYYY
        dm = DATE_RE.search(search_text)
        if dm:
            links[dm.group(1)] = _clean_url(href)
            continue

        # Try DD Month YYYY
        dm = DATE_LONG_RE.search(search_text)
        if dm:
            day = int(dm.group(1))
            month_str = dm.group(2).lower()
            year = int(dm.group(3))
            month = MONTH_MAP.get(month_str)
            if month:
                date_str = f"{day:02d}-{month:02d}-{year}"
                if date_str not in links:
                    links[date_str] = _clean_url(href)

    return links


async def find_download_link(source_url: str, dates_to_try: list[date]) -> tuple[date, str] | None:
    """Return ``(date, google_drive_url)`` for the first available date in
    ``dates_to_try``, or ``None``. No download — just the link.

    This is the single source of truth for "find the link for a date", used by
    the scheduler, /get and the web UI. Reuses :func:`fetch_page` + :func:`parse_links`.
    """
    try:
        loop = asyncio.get_running_loop()
        html = await loop.run_in_executor(None, lambda: fetch_page(source_url))
    except Exception as e:
        logger.error("Failed to fetch %s: %s", source_url, e)
        return None

    links = parse_links(html)  # {DD-MM-YYYY: drive_url}
    if not links:
        return None

    for d in dates_to_try:
        url = links.get(d.strftime("%d-%m-%Y"))
        if url:
            return d, url
    return None


async def get_latest_download_link(source_url: str, days_back: int = 10) -> tuple[date, str] | None:
    """Link for the most recent available edition within ``days_back`` days."""
    today = get_today()
    return await find_download_link(source_url, [today - timedelta(days=i) for i in range(days_back + 1)])
