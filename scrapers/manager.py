"""
Scraper orchestration layer.

:class:`ScraperManager` holds all registered :class:`BaseScraper` instances
and delegates ``get_edition`` calls to them, handling retries and fallback
across multiple sources transparently.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Optional

import httpx

from scrapers.base import BaseScraper, EditionResult

logger = logging.getLogger(__name__)


class ScraperManager:
    """Registry + dispatcher for newspaper scrapers.

    Usage::

        manager = ScraperManager()
        manager.register(MySourceScraper())
        result = await manager.get_edition("times-of-india", date.today())
    """

    def __init__(self, scrapers: Optional[list[BaseScraper]] = None) -> None:
        self._scrapers: list[BaseScraper] = list(scrapers or [])

    # ── registration ─────────────────────────────────────────────────

    def register(self, scraper: BaseScraper) -> None:
        """Add a scraper to the registry."""
        if any(s.source_name == scraper.source_name for s in self._scrapers):
            logger.warning(
                "Scraper '%s' is already registered — skipping duplicate.",
                scraper.source_name,
            )
            return
        self._scrapers.append(scraper)
        logger.info("Registered scraper: %s", scraper.source_name)

    @property
    def scrapers(self) -> list[BaseScraper]:
        return list(self._scrapers)

    # ── edition lookup ───────────────────────────────────────────────

    async def get_edition(
        self,
        title_slug: str,
        edition_date: date,
        *,
        max_retries: int = 2,
        retry_delay: float = 3.0,
    ) -> Optional[EditionResult]:
        """Try every registered scraper until one returns an edition.

        Each scraper is attempted up to *max_retries* times with an
        exponential back-off starting at *retry_delay* seconds.
        """
        for scraper in self._scrapers:
            for attempt in range(1, max_retries + 1):
                try:
                    result = await scraper.get_edition(title_slug, edition_date)
                    if result is not None:
                        logger.info(
                            "[%s] Found edition %s for %s",
                            scraper.source_name,
                            edition_date,
                            title_slug,
                        )
                        return result
                    # Scraper returned None → edition not available from this source
                    break
                except Exception:
                    logger.exception(
                        "[%s] Attempt %d/%d failed for %s on %s",
                        scraper.source_name,
                        attempt,
                        max_retries,
                        title_slug,
                        edition_date,
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(retry_delay * attempt)

        logger.warning(
            "No scraper could provide %s for %s", title_slug, edition_date
        )
        return None

    # ── download helpers ─────────────────────────────────────────────

    async def download_to_memory(
        self,
        url: str,
        *,
        timeout: float = 120.0,
    ) -> Optional[bytes]:
        """Download a URL entirely into memory.

        This is source-agnostic — it does not go through any particular
        scraper.  Useful once you already have a ``download_url`` from an
        :class:`EditionResult`.
        """
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=timeout,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"
                    ),
                },
            ) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    return response.content
                logger.warning(
                    "download_to_memory got HTTP %d for %s",
                    response.status_code,
                    url,
                )
        except httpx.HTTPError:
            logger.exception("download_to_memory failed for %s", url)
        return None

    async def download_to_file(
        self,
        url: str,
        dest_path: str,
        *,
        timeout: float = 120.0,
    ) -> bool:
        """Stream a URL to disk. Returns ``True`` on success."""
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=timeout,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"
                    ),
                },
            ) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        return False
                    with open(dest_path, "wb") as fh:
                        async for chunk in response.aiter_bytes(65_536):
                            fh.write(chunk)
            return True
        except (httpx.HTTPError, OSError):
            logger.exception("download_to_file failed for %s", url)
            return False
