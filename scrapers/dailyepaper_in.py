import re
import asyncio
import logging
import urllib.request
from bs4 import BeautifulSoup
from datetime import date, timedelta
from utils.helpers import get_today

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/114.0.0.0 Safari/537.36"


async def _fetch_html(source_url: str) -> str:
    """Fetch a page's HTML off the event loop (urllib in an executor)."""
    req = urllib.request.Request(source_url, headers={"User-Agent": _UA})

    def fetch():
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.read().decode("utf-8")

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fetch)


def _date_matches(text: str, d: date) -> bool:
    """Flexible date check: handles '12 Jun', '12th June', '12-06-2026', etc."""
    nums = set(re.findall(r"\d+", text))
    has_day = str(d.day) in nums or f"{d.day:02d}" in nums
    has_month_text = d.strftime("%b").lower() in text or d.strftime("%B").lower() in text
    has_month_num = str(d.month) in nums or f"{d.month:02d}" in nums
    has_year = str(d.year) in nums
    return has_day and (has_month_text or has_month_num) and has_year


def _find_drive_url(soup: BeautifulSoup, dates_to_try: list[date]) -> tuple[str, date] | None:
    """Find the Google Drive URL for the first matching date in `dates_to_try`.

    Returns ``(drive_url, date)`` or ``None``. Shared by :func:`scrape` and
    :func:`get_latest_download_link` so both use identical matching logic.
    """
    for d in dates_to_try:
        # 1. Standard <a> tags with Google Drive links
        for a in soup.find_all("a", href=True):
            if "drive.google.com/file/d/" in a["href"]:
                parent_text = a.parent.get_text(strip=True).lower()
                if _date_matches(parent_text, d):
                    return a["href"], d

        # 2. Ninja Tables / raw cells with Google Drive links
        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) >= 2:
                drive_url = None
                date_cell_text = ""
                for td in tds:
                    text = td.get_text(strip=True)
                    if "drive.google.com/file/d/" in text:
                        drive_url = text
                    elif td.find("a", href=True) and "drive.google.com/file/d/" in td.find("a", href=True).get("href", ""):
                        drive_url = td.find("a", href=True)["href"]
                    else:
                        date_cell_text += " " + text.lower()

                if drive_url and _date_matches(date_cell_text, d):
                    return drive_url, d
    return None


async def find_download_link(source_url: str, dates_to_try: list[date]) -> tuple[date, str] | None:
    """Return ``(date, google_drive_url)`` for the first available date in
    ``dates_to_try``, or ``None``. No download — just the link.

    Single source of truth for "find the link for a date", used by the
    scheduler, /get and the web UI. Reuses :func:`_find_drive_url`.
    """
    try:
        html = await _fetch_html(source_url)
    except Exception as e:
        logger.error("Failed to fetch %s: %s", source_url, e)
        return None

    soup = BeautifulSoup(html, "html.parser")
    found = _find_drive_url(soup, dates_to_try)
    if not found:
        return None
    drive_url, d = found
    return d, drive_url


async def get_latest_download_link(source_url: str, days_back: int = 10) -> tuple[date, str] | None:
    """Link for the most recent available edition within ``days_back`` days."""
    today = get_today()
    return await find_download_link(source_url, [today - timedelta(days=i) for i in range(days_back + 1)])
