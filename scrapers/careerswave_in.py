import os
import re
import asyncio
import logging
import httpx
import gdown
import urllib.request
from bs4 import BeautifulSoup
from datetime import date, datetime, timedelta
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


async def download_from_gdrive(file_id: str, output_file: str, name: str) -> bool:
    """Downloads a file from Google Drive using direct HTTP GET with confirmation bypass,
    falling back to gdown if it fails.
    """
    url = "https://docs.google.com/uc?export=download"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    }
    
    logger.info("[%s] Attempting direct HTTP download from Google Drive...", name)
    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=120.0) as client:
            resp = await client.get(url, params={"id": file_id})
            
            token = None
            for cookie_name, cookie_val in resp.cookies.items():
                if cookie_name.startswith("download_warning"):
                    token = cookie_val
                    break
                    
            if token:
                logger.info("[%s] Large file warning received. Confirming download...", name)
                resp = await client.get(url, params={"id": file_id, "confirm": token})
                
            if resp.status_code == 200 and resp.content.startswith(b"%PDF"):
                with open(output_file, "wb") as f:
                    f.write(resp.content)
                logger.info("[%s] Direct HTTP download succeeded (%d bytes).", name, len(resp.content))
                return True
            else:
                logger.info("[%s] Direct download did not return a valid PDF (status: %d).", name, resp.status_code)
    except Exception as e:
        logger.warning("[%s] Direct HTTP download failed: %s", name, e)
        
    logger.info("[%s] Falling back to gdown download...", name)
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: gdown.download(id=file_id, output=output_file, quiet=True))
        if os.path.exists(output_file) and os.path.getsize(output_file) > 1000:
            with open(output_file, "rb") as f:
                magic = f.read(4)
            if magic == b"%PDF":
                logger.info("[%s] gdown fallback download succeeded.", name)
                return True
    except Exception as e:
        logger.warning("[%s] gdown fallback download failed: %s", name, e)
        
    return False

async def scrape(source_url: str, slug: str, name: str, target_date: date = None) -> tuple[str, date] | None:
    """
    Scrapes the careerswave.in website for a given newspaper.
    Returns the absolute path to the downloaded PDF and the date, or None if failed.
    
    Supports both the old table format (DD-MM-YYYY) and the new paragraph
    format (DD Month YYYY) used by careerswave.in.
    """
    logger.info("[%s] Fetching %s...", name, source_url)
    try:
        loop = asyncio.get_running_loop()
        html = await loop.run_in_executor(None, lambda: fetch_page(source_url))
    except Exception as e:
        logger.error("[%s] Failed to fetch page: %s", name, e)
        return None

    links = parse_links(html)
    if not links:
        logger.info("[%s] No dated links found on the page.", name)
        return None

    logger.info("[%s] Found %d dated links on the page.", name, len(links))

    base_date = target_date or get_today()
    dates_to_try = [base_date]
    if not target_date:
        dates_to_try.append(base_date - timedelta(days=1))
        dates_to_try.append(base_date - timedelta(days=2))
        dates_to_try.append(base_date - timedelta(days=3))

    for d in dates_to_try:
        date_str = d.strftime("%d-%m-%Y")
        if date_str in links:
            view_url = links[date_str]
            match = re.search(r'/d/([a-zA-Z0-9_-]+)', view_url) or re.search(r'[?&]id=([a-zA-Z0-9_-]+)', view_url)
            if not match:
                logger.warning("[%s] Could not extract Google Drive file ID from %s", name, view_url)
                continue
                
            file_id = match.group(1)
            output_file = f"{slug}_{d.strftime('%Y-%m-%d')}.pdf"
            
            if not await download_from_gdrive(file_id, output_file, name):
                logger.warning("[%s] Failed to download PDF for %s", name, date_str)
                continue
                
            return os.path.abspath(output_file), d

    logger.info("[%s] No edition found for any of the dates: %s", name, [d.strftime('%Y-%m-%d') for d in dates_to_try])
    return None
