"""
Template scraper — copy this file to create a new source.

This file is intentionally full of TODO markers and detailed comments so
that a developer new to the codebase can fill it in quickly.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Optional


from scrapers.base import BaseScraper, EditionResult, TitleInfo

logger = logging.getLogger(__name__)


class TemplateScraper(BaseScraper):
    """Scraper for **TODO: source website name**.

    URL pattern
    -----------
    Most newspaper-archive sites follow one of these URL schemes:

    * ``https://example.com/<slug>/<YYYY-MM-DD>.pdf``
    * ``https://example.com/editions/<slug>?date=DD-MM-YYYY``
    * ``https://example.com/api/v1/editions?title=<id>&date=<epoch>``

    TODO: document the URL pattern you reverse-engineered here.
    """

    # TODO: Change the base URL to the actual website.
    BASE_URL = "https://example.com"

    # TODO: Some sites use a different slug mapping than ours.
    # If the source uses its own identifiers, build a dict here that maps
    # our config slugs to the source's identifiers:
    #
    # SLUG_MAP: dict[str, str] = {
    #     "times-of-india": "toi",
    #     "hindustan-times": "ht",
    # }

    # ── BaseScraper interface ────────────────────────────────────────

    @property
    def source_name(self) -> str:
        # TODO: Return a short, unique identifier for this source,
        # e.g. "tradingref", "freedailypapers", etc.
        return "template"

    async def get_catalog(self) -> list[TitleInfo]:
        """Fetch the list of available titles from the source website.

        TODO: Implement this by:
        1. Making an HTTP GET to the catalogue / index page.
        2. Parsing the HTML with BeautifulSoup to extract title names,
           slugs, and languages.
        3. Wrapping each into a ``TitleInfo`` dataclass and returning.

        Example
        -------
        >>> async with httpx.AsyncClient() as client:
        ...     r = await client.get(f"{self.BASE_URL}/newspapers")
        ...     soup = BeautifulSoup(r.text, "html.parser")
        ...     for link in soup.select("ul.papers li a"):
        ...         name = link.get_text(strip=True)
        ...         slug = link["href"].rstrip("/").split("/")[-1]
        ...         results.append(TitleInfo(name=name, slug=slug,
        ...                                 language="English",
        ...                                 category="Newspaper"))
        """
        results: list[TitleInfo] = []

        # TODO: Replace the example below with actual scraping logic.
        #
        # try:
        #     async with httpx.AsyncClient(
        #         headers=self._DEFAULT_HEADERS,
        #         timeout=30,
        #     ) as client:
        #         response = await client.get(f"{self.BASE_URL}/newspapers")
        #         response.raise_for_status()
        #
        #     soup = BeautifulSoup(response.text, "html.parser")
        #
        #     for card in soup.select("div.paper-card"):
        #         name = card.select_one("h3").get_text(strip=True)
        #         slug = card["data-slug"]
        #         language = card.get("data-language", "English")
        #         results.append(
        #             TitleInfo(
        #                 name=name,
        #                 slug=slug,
        #                 language=language,
        #                 category="Newspaper",
        #             )
        #         )
        # except httpx.HTTPError:
        #     logger.exception("Failed to fetch catalogue from %s", self.BASE_URL)

        return results

    async def get_edition(
        self, title_slug: str, edition_date: date
    ) -> Optional[EditionResult]:
        """Try to locate a downloadable PDF for *title_slug* on *edition_date*.

        TODO: Implement by:
        1. Building the expected URL for the given slug + date.
           Many sites use patterns like ``/slug/YYYY/MM/DD/paper.pdf``.
        2. Making a HEAD or GET request to confirm the PDF exists
           (check Content-Type and status code).
        3. Returning an ``EditionResult`` with the download URL, or
           ``None`` if the edition is not available yet.

        Example
        -------
        >>> date_str = edition_date.strftime("%Y-%m-%d")
        >>> url = f"{self.BASE_URL}/dl/{title_slug}/{date_str}.pdf"
        >>> async with httpx.AsyncClient() as client:
        ...     head = await client.head(url, follow_redirects=True)
        ...     if head.status_code == 200:
        ...         size_bytes = int(head.headers.get("content-length", 0))
        ...         return EditionResult(
        ...             title_slug=title_slug,
        ...             edition_date=edition_date,
        ...             download_url=url,
        ...             file_size_mb=round(size_bytes / 1_048_576, 2) or None,
        ...         )
        ...     return None

        Tips
        ----
        * Always set a realistic ``User-Agent`` header (the base class
          provides ``self._DEFAULT_HEADERS``).
        * Some sites return 403 without a ``Referer`` header — try
          setting ``Referer: https://example.com/``.
        * If the site uses JavaScript rendering, you may need to look for
          an underlying API endpoint in the browser Network tab instead.
        * Use ``HEAD`` first to avoid downloading a full PDF just to
          check existence (``GET`` is fine for small pages).
        """

        # TODO: Uncomment and adapt:
        #
        # date_str = edition_date.strftime("%d-%m-%Y")
        # url = f"{self.BASE_URL}/pdf/{title_slug}/{date_str}.pdf"
        #
        # try:
        #     async with httpx.AsyncClient(
        #         headers=self._DEFAULT_HEADERS,
        #         timeout=30,
        #         follow_redirects=True,
        #     ) as client:
        #         head = await client.head(url)
        #
        #     if head.status_code == 200:
        #         content_type = head.headers.get("content-type", "")
        #         if "pdf" in content_type or url.endswith(".pdf"):
        #             size_bytes = int(head.headers.get("content-length", 0))
        #             return EditionResult(
        #                 title_slug=title_slug,
        #                 edition_date=edition_date,
        #                 download_url=url,
        #                 file_size_mb=round(size_bytes / 1_048_576, 2) or None,
        #             )
        # except httpx.HTTPError:
        #     logger.exception("get_edition failed for %s %s", title_slug, edition_date)

        return None


# ── standalone test block ────────────────────────────────────────────

if __name__ == "__main__":
    """Quick smoke test — run with: python -m scrapers.template_scraper

    TODO: After implementing the methods above, this block lets you
    verify your scraper works end-to-end without starting the full bot.
    """
    import pprint
    from datetime import date as _date

    async def _test() -> None:
        scraper = TemplateScraper()
        print(f"Source: {scraper.source_name}\n")

        # 1. Test catalogue
        print("── Catalogue ─────────────────────────")
        catalog = await scraper.get_catalog()
        if catalog:
            for t in catalog[:10]:
                print(f"  {t.language:12s} | {t.slug:30s} | {t.name}")
            print(f"  … {len(catalog)} titles total")
        else:
            print("  (empty — implement get_catalog first)")

        # 2. Test edition lookup
        print("\n── Edition lookup ────────────────────")
        test_slug = "times-of-india"  # TODO: change to a slug your source supports
        test_date = _date.today()
        result = await scraper.get_edition(test_slug, test_date)
        if result:
            pprint.pprint(result)
        else:
            print(f"  No edition found for {test_slug} on {test_date}")

        # 3. Test download (uncomment when get_edition works)
        # if result:
        #     print("\n── Download test ─────────────────────")
        #     data = await scraper.download_to_bytes(result.download_url)
        #     if data:
        #         print(f"  Downloaded {len(data) / 1_048_576:.1f} MB")
        #     else:
        #         print("  Download failed")

    asyncio.run(_test())
