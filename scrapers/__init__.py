# scrapers package
"""Shared newspaper link finder with automatic dailyepaper.in fallback."""

import importlib
import logging
from datetime import date

from scrapers import dailyepaper_in

logger = logging.getLogger(__name__)


async def find_newspaper_link(
    title_name: str, scrape_website: str, source_url: str, dates_to_try: list[date]
) -> tuple[date, str] | None:
    """Return ``(date, url)`` for the newest available date across sources.

    Tries the title's configured source first. If it misses the newest wanted
    date (``dates_to_try[0]``, lists are newest-first), also checks the paper's
    auto-discovered dailyepaper.in page and keeps whichever hit is newer — so a
    paper missing from the primary through the day still arrives.

    ``title_name`` is forwarded to the scraper module so sources that list
    multiple titles on one page (e.g. indiags.com) know which card to pick.
    """
    module = importlib.import_module(f"scrapers.{scrape_website}")
    best = await module.find_download_link(source_url, dates_to_try, title_name=title_name)
    if best and best[0] == dates_to_try[0]:
        return best

    if scrape_website != "dailyepaper_in":
        page_url = await dailyepaper_in.find_title_page(title_name)
        if page_url:
            alt = await dailyepaper_in.find_download_link(page_url, dates_to_try)
            if alt and (best is None or alt[0] > best[0]):
                logger.info("[%s] Using dailyepaper.in fallback: %s", title_name, page_url)
                return alt
    return best
