"""
Abstract base class and shared data structures for newspaper scrapers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import httpx


# ── data structures ──────────────────────────────────────────────────

@dataclass(frozen=True)
class TitleInfo:
    """Metadata for a single newspaper / magazine title."""

    name: str
    slug: str
    language: str
    category: str


@dataclass
class EditionResult:
    """Result returned by a scraper when an edition is found."""

    title_slug: str
    edition_date: date
    download_url: str
    file_size_mb: Optional[float] = None
    extra: dict = field(default_factory=dict)


# ── abstract scraper ─────────────────────────────────────────────────

class BaseScraper(ABC):
    """Interface that every concrete scraper must implement.

    Sub-classes are responsible for knowing how to talk to one external
    source (website / API) and translating its responses into the
    standard :class:`TitleInfo` / :class:`EditionResult` structures.
    """

    # Shared httpx defaults
    _DEFAULT_HEADERS: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
    }
    _DEFAULT_TIMEOUT: float = 120.0

    # ── abstract members ────────────────────────────────────────────

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Unique identifier for this scraper source (e.g. ``'tradingref'``)."""
        ...

    @abstractmethod
    async def get_catalog(self) -> list[TitleInfo]:
        """Return every title this source can provide."""
        ...

    @abstractmethod
    async def get_edition(
        self, title_slug: str, edition_date: date
    ) -> Optional[EditionResult]:
        """Attempt to locate a downloadable edition.

        Returns ``None`` if the edition is not (yet) available.
        """
        ...

    # ── default download implementation ─────────────────────────────

    async def download_pdf(self, url: str, dest_path: str) -> bool:
        """Download a PDF from *url* to *dest_path*.

        The default implementation streams the response to disk using
        ``httpx``.  Override in a subclass if the source requires
        cookies, authentication, or other special handling.

        Returns ``True`` on success, ``False`` on any error.
        """
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=self._DEFAULT_TIMEOUT,
                headers=self._DEFAULT_HEADERS,
            ) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        return False
                    with open(dest_path, "wb") as fh:
                        async for chunk in response.aiter_bytes(chunk_size=65_536):
                            fh.write(chunk)
            return True
        except (httpx.HTTPError, OSError) as exc:
            # Intentionally swallowed — caller decides what to do next
            import logging

            logging.getLogger(__name__).warning(
                "download_pdf failed for %s: %s", url, exc
            )
            return False

    async def download_to_bytes(self, url: str) -> Optional[bytes]:
        """Download a URL entirely into memory and return the bytes.

        Returns ``None`` on failure.
        """
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=self._DEFAULT_TIMEOUT,
                headers=self._DEFAULT_HEADERS,
            ) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    return response.content
        except httpx.HTTPError:
            pass
        return None
