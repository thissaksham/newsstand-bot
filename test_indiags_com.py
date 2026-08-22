"""
Unit tests for scrapers/indiags_com.py parsing logic.

These tests use synthetic HTML so they do not require network access to
indiags.com (which is unreachable from many datacenter environments).
"""

import asyncio
from datetime import date
from unittest.mock import patch

from scrapers import indiags_com


def test_extract_book_id():
    assert indiags_com._extract_book_id("https://www.indiags.com/epaper/books/2265") == "2265"
    assert indiags_com._extract_book_id("/epaper/books/42") == "42"
    assert indiags_com._extract_book_id("https://example.com") is None


def test_title_matches():
    assert indiags_com._title_matches("The Hindu", "The Hindu")
    assert indiags_com._title_matches("The Hindu Delhi", "The Hindu")
    assert indiags_com._title_matches("Indian Express", "Indian Express")
    assert not indiags_com._title_matches("The Hindu", "Indian Express")


def test_find_go_link_in_banner():
    html = """
    <html><body>
      <div id="pdfUnlockBanner">
        <a href="/go/ayuJ43XuUX8vZEAwASf7OZ4ogNYt4zkD2Dzgo2kQ">Download</a>
      </div>
    </body></html>
    """
    link = indiags_com._find_go_link(html)
    assert link == "https://www.indiags.com/go/ayuJ43XuUX8vZEAwASf7OZ4ogNYt4zkD2Dzgo2kQ"


def test_find_go_link_absolute():
    html = """
    <html><body>
      <div id="pdfUnlockBanner">
        <a href="https://www.indiags.com/go/abc123">Download</a>
      </div>
    </body></html>
    """
    link = indiags_com._find_go_link(html)
    assert link == "https://www.indiags.com/go/abc123"


def test_parse_listing_page():
    html = """
    <html><body>
      <div class="ep-grid">
        <div class="ep-card">
          <div class="title">The Hindu</div>
          <div class="foot"><a href="/epaper/books/2265">Read</a></div>
        </div>
        <div class="ep-card">
          <div class="title">Indian Express</div>
          <div class="foot"><a href="/epaper/books/2266">Read</a></div>
        </div>
        <div class="ep-card">
          <div class="title">Business Standard</div>
          <div class="foot"><a href="/epaper/books/2267">Read</a></div>
        </div>
      </div>
    </body></html>
    """
    papers = asyncio.run(indiags_com._parse_listing_page(html))
    assert len(papers) == 3
    slugs = {p["book_id"]: p["name"] for p in papers}
    assert slugs["2265"] == "The Hindu"
    assert slugs["2266"] == "Indian Express"
    assert slugs["2267"] == "Business Standard"


def test_get_latest_download_link_cached():
    """When the listing page is mocked, get_latest_download_link should pick the
    right card, visit the open page, and return the go-link."""
    listing_html = """
    <html><body>
      <div class="ep-grid">
        <div class="ep-card">
          <div class="title">The Hindu</div>
          <div class="foot"><a href="/epaper/books/2265">Read</a></div>
        </div>
      </div>
    </body></html>
    """
    open_html = """
    <html><body>
      <div id="pdfUnlockBanner">
        <a href="/go/abc123">Download</a>
      </div>
    </body></html>
    """

    async def fake_fetch(url: str, timeout: float = 30.0) -> str:
        return listing_html

    async def fake_open_link(book_id: str) -> str:
        return "https://www.indiags.com/go/abc123"

    # Clear the listing cache so the mock is used.
    indiags_com._listing_cache = (0.0, [])

    with patch.object(indiags_com, "_fetch_html", fake_fetch), \
         patch.object(indiags_com, "_fetch_open_page_link", fake_open_link):
        result = asyncio.run(
            indiags_com.get_latest_download_link(
                "https://www.indiags.com/epaper-pdf-download",
                title_name="The Hindu",
            )
        )
    assert result is not None
    d, url = result
    assert isinstance(d, date)
    assert url == "https://www.indiags.com/go/abc123"


if __name__ == "__main__":
    test_extract_book_id()
    test_title_matches()
    test_find_go_link_in_banner()
    test_find_go_link_absolute()
    test_parse_listing_page()
    test_get_latest_download_link_cached()
    print("test_indiags_com: OK")
