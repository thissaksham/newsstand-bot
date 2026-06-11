# Writing Your Own Scraper — Developer Guide

This guide walks you through creating a new scraper for the Newsstand Bot.
By the end you will have a working subclass of `BaseScraper` that can
discover titles and download PDF editions from a new source website.

---

## 1. Understanding the Scraper Interface

Every scraper must extend `scrapers.base.BaseScraper` and implement three
things:

| Member | Type | Purpose |
|---|---|---|
| `source_name` | `@property → str` | Unique identifier (e.g. `"mysite"`) |
| `get_catalog()` | `async → list[TitleInfo]` | Return every title this source offers |
| `get_edition(slug, date)` | `async → EditionResult \| None` | Find the download URL for one edition |

The base class already provides `download_pdf(url, path)` and
`download_to_bytes(url)` — override them only if the source needs
cookies or custom auth.

### Data structures

```python
@dataclass
class TitleInfo:
    name: str        # "The Times of India"
    slug: str        # "times-of-india"
    language: str    # "English"
    category: str    # "Newspaper"

@dataclass
class EditionResult:
    title_slug: str        # matches our config slug
    edition_date: date
    download_url: str      # direct URL to the PDF
    file_size_mb: float | None
```

---

## 2. How to Inspect a Website

Before writing code, reverse-engineer the site using your browser's DevTools.

### Step-by-step

1. **Open DevTools** → Network tab → check "Preserve log".
2. Navigate to the site's newspaper listing page.
3. Look for the **XHR/Fetch** requests — many sites load data via a
   JSON API even though the page looks like plain HTML.
4. Click on a specific edition / PDF link and observe:
   - The final URL (after redirects).
   - Query parameters (`?date=...&paper=...`).
   - Required request headers (`Referer`, `Cookie`, etc.).
5. Right-click the page → **View Page Source** to understand the HTML
   structure for BeautifulSoup selectors.

### What to note

| Detail | Why it matters |
|---|---|
| Base URL | Root domain for constructing URLs |
| URL pattern | How date and title slug appear in the URL |
| Date format | `YYYY-MM-DD`, `DD-MM-YYYY`, epoch, etc. |
| Auth / cookies | Whether a session cookie is needed |
| Content-Type | Confirm the response is `application/pdf` |
| Rate limits | Any `429` responses or CAPTCHAs |

---

## 3. Common URL Patterns

Most newspaper-archive sites follow one of these patterns:

```
# Pattern A – date in path
https://example.com/pdf/{slug}/{YYYY-MM-DD}.pdf

# Pattern B – date as query param
https://example.com/download?paper={slug}&date={DD-MM-YYYY}

# Pattern C – numeric IDs
https://example.com/api/editions/{paper_id}?date={epoch_ms}

# Pattern D – directory listing
https://cdn.example.com/editions/{slug}/{YYYY}/{MM}/{DD}/edition.pdf
```

> **Tip:** Use a HEAD request first to check if the PDF exists without
> downloading the full file.

---

## 4. Step-by-Step: Writing Your First Scraper

### 4.1 Copy the template

```bash
cp scrapers/template_scraper.py scrapers/mysite_scraper.py
```

### 4.2 Set `source_name`

```python
@property
def source_name(self) -> str:
    return "mysite"
```

### 4.3 Implement `get_catalog()`

```python
async def get_catalog(self) -> list[TitleInfo]:
    async with httpx.AsyncClient(headers=self._DEFAULT_HEADERS) as client:
        r = await client.get("https://mysite.com/papers")
    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for row in soup.select("table.papers-list tr"):
        cols = row.find_all("td")
        if len(cols) < 3:
            continue
        name = cols[0].get_text(strip=True)
        slug = cols[0].a["href"].split("/")[-1]
        language = cols[1].get_text(strip=True)
        results.append(TitleInfo(name=name, slug=slug,
                                 language=language, category="Newspaper"))
    return results
```

### 4.4 Implement `get_edition()`

```python
async def get_edition(self, title_slug: str,
                      edition_date: date) -> EditionResult | None:
    url = (
        f"https://mysite.com/pdf/{title_slug}/"
        f"{edition_date.strftime('%Y-%m-%d')}.pdf"
    )
    async with httpx.AsyncClient(
        headers=self._DEFAULT_HEADERS, follow_redirects=True
    ) as client:
        head = await client.head(url)

    if head.status_code != 200:
        return None

    size = int(head.headers.get("content-length", 0))
    return EditionResult(
        title_slug=title_slug,
        edition_date=edition_date,
        download_url=url,
        file_size_mb=round(size / 1_048_576, 2) if size else None,
    )
```

### 4.5 Register your scraper

In `main.py` (or wherever you initialise the bot):

```python
from scrapers.manager import ScraperManager
from scrapers.mysite_scraper import MySiteScraper

manager = ScraperManager()
manager.register(MySiteScraper())
```

---

## 5. Testing Your Scraper

### Standalone smoke test

Every scraper file should include a `__main__` block (the template
already has one):

```bash
python -m scrapers.mysite_scraper
```

### What to verify

- [ ] `get_catalog()` returns the expected number of titles.
- [ ] `get_edition()` returns a valid URL for today's date.
- [ ] `download_to_bytes()` actually fetches PDF bytes (check the first
      few bytes equal `%PDF`).
- [ ] A date with no edition returns `None` (not an exception).
- [ ] The scraper handles network errors gracefully (try with Wi-Fi off).

### Unit test pattern

```python
import pytest
from unittest.mock import AsyncMock, patch
from scrapers.mysite_scraper import MySiteScraper

@pytest.mark.asyncio
async def test_get_edition_returns_none_for_missing():
    scraper = MySiteScraper()
    with patch("httpx.AsyncClient.head", new_callable=AsyncMock) as mock:
        mock.return_value.status_code = 404
        result = await scraper.get_edition("nonexistent", date(2026, 1, 1))
    assert result is None
```

---

## 6. Common Issues

### 6.1 `403 Forbidden`

The server rejects requests without a browser-like `User-Agent` or
`Referer` header.

**Fix:** Use `self._DEFAULT_HEADERS` (already set in `BaseScraper`), or
add a `Referer`:

```python
headers = {**self._DEFAULT_HEADERS, "Referer": "https://example.com/"}
```

### 6.2 Cookies / sessions

Some sites require you to visit the home page first to get a session
cookie.

**Fix:** Make a GET to the home page, capture cookies, then reuse:

```python
async with httpx.AsyncClient(headers=self._DEFAULT_HEADERS) as client:
    await client.get("https://example.com")  # sets cookies
    response = await client.get(pdf_url)     # cookies sent automatically
```

### 6.3 JavaScript-rendered pages

If `View Page Source` is nearly empty but the page shows content, the
data is loaded via JavaScript.

**Fix:** Look in the Network tab for the underlying XHR/Fetch API call —
that is usually a simple JSON endpoint you can hit directly with httpx.

### 6.4 Rate limiting / `429 Too Many Requests`

**Fix:** Add a delay between requests:

```python
import asyncio
await asyncio.sleep(1.5)  # be polite
```

### 6.5 Redirects to login page

The server returns 200 but the body is an HTML login form, not a PDF.

**Fix:** Check `Content-Type` of the response:

```python
ct = response.headers.get("content-type", "")
if "pdf" not in ct:
    return None  # not a real PDF
```

---

## 7. Full Example: Hypothetical "FreeNewsPDFs" Scraper

```python
"""scrapers/freenewspdfs_scraper.py"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, EditionResult, TitleInfo

logger = logging.getLogger(__name__)


class FreeNewsPdfsScraper(BaseScraper):
    BASE_URL = "https://freenewspdfs.example.com"

    @property
    def source_name(self) -> str:
        return "freenewspdfs"

    async def get_catalog(self) -> list[TitleInfo]:
        async with httpx.AsyncClient(headers=self._DEFAULT_HEADERS) as client:
            r = await client.get(f"{self.BASE_URL}/catalog")
        soup = BeautifulSoup(r.text, "html.parser")
        titles: list[TitleInfo] = []
        for card in soup.select("div.paper"):
            titles.append(
                TitleInfo(
                    name=card.select_one("h4").get_text(strip=True),
                    slug=card["data-slug"],
                    language=card.get("data-lang", "English"),
                    category="Newspaper",
                )
            )
        return titles

    async def get_edition(
        self, title_slug: str, edition_date: date
    ) -> Optional[EditionResult]:
        url = (
            f"{self.BASE_URL}/dl/"
            f"{title_slug}/{edition_date.strftime('%Y/%m/%d')}/paper.pdf"
        )
        try:
            async with httpx.AsyncClient(
                headers=self._DEFAULT_HEADERS, follow_redirects=True
            ) as client:
                head = await client.head(url, timeout=15)
            if head.status_code == 200:
                ct = head.headers.get("content-type", "")
                if "pdf" in ct or url.endswith(".pdf"):
                    size = int(head.headers.get("content-length", 0))
                    return EditionResult(
                        title_slug=title_slug,
                        edition_date=edition_date,
                        download_url=url,
                        file_size_mb=round(size / 1_048_576, 2) if size else None,
                    )
        except httpx.HTTPError:
            logger.exception("Failed for %s on %s", title_slug, edition_date)
        return None
```

---

## Quick Reference Checklist

- [ ] Copied `template_scraper.py` as starting point
- [ ] Set a unique `source_name`
- [ ] Implemented `get_catalog()` with real HTML parsing
- [ ] Implemented `get_edition()` with correct URL pattern + date format
- [ ] Handled 404 / 403 / missing editions gracefully (return `None`)
- [ ] Tested standalone with `python -m scrapers.my_scraper`
- [ ] Registered in `ScraperManager`
