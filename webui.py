"""
Newsstand Bot — Web Testing UI

A standalone FastAPI app that drives the SAME config + scraper logic the bot
uses, for manual testing in a browser. No database and no Telegram involved —
it only reads config.yaml and calls the scraper modules.

Flow (mirrors the bot):
- English / Hindi → list newspapers from config.yaml → pick one → show the
  latest available download link (Google Drive), via the scraper's
  ``get_latest_download_link`` (no PDF download).
- Magazine → type a name → ``search_magazines`` (downmagaz.net) → pick a result
  → ``scrape_magazine_tag`` + ``matches_version`` + ``get_download_links`` to
  show the latest available version's download links.

Run::

    pip install -r requirements-web.txt
    python webui.py            # → http://127.0.0.1:8000
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from config import Config
from scrapers import find_newspaper_link
from utils.helpers import format_date, get_today
from scrapers.downmagaz_net import (
    search_magazines,
    scrape_magazine_tag,
    matches_version,
    get_download_links,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger("webui")

_HERE = Path(__file__).resolve().parent
app = FastAPI(title="Newsstand Bot — Test UI")


# ─── Page ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (_HERE / "web" / "index.html").read_text(encoding="utf-8")


# ─── Newspapers (English / Hindi) ──────────────────────────────────────────

@app.get("/api/newspapers")
async def api_newspapers(language: str = Query(..., description="English or Hindi")):
    """List newspapers for a language straight from config.yaml."""
    titles = Config.get().get_titles_by_language(language)
    return [{"slug": t.slug, "name": t.name} for t in titles]


@app.get("/api/category-titles")
async def api_category_titles(category: str = Query(..., description="Category name from config.yaml")):
    """List titles for a non-language category straight from config.yaml."""
    titles = [t for t in Config.get().titles if getattr(t, "category", "Newspaper") == category]
    return [{"slug": t.slug, "name": t.name} for t in titles]


@app.get("/api/newspaper-link")
async def api_newspaper_link(slug: str = Query(...)):
    """Scrape the source page and return the latest available download link."""
    title = Config.get().get_title(slug)
    if not title:
        raise HTTPException(status_code=404, detail="Unknown newspaper.")
    if not title.source_url or not title.scrape_website:
        raise HTTPException(status_code=400, detail="This title has no source configured.")

    try:
        today = get_today()
        result = await find_newspaper_link(
            title.name, title.scrape_website, title.source_url,
            [today - timedelta(days=i) for i in range(11)],
        )
    except Exception as e:
        logger.exception("Link lookup failed for %s", slug)
        raise HTTPException(status_code=502, detail=f"Scrape failed: {e}")

    if not result:
        return {"found": False, "name": title.name}
    d, url = result
    label = "Google Drive"
    url_l = url.lower()
    if "indiags.com" in url_l:
        label = "indiags.com"
    elif "drive.google.com" not in url_l and "google.com" not in url_l:
        label = "source"
    return {"found": True, "name": title.name, "date": format_date(d), "url": url, "label": label}


# ─── Magazines ─────────────────────────────────────────────────────────────

@app.get("/api/magazines/search")
async def api_magazine_search(q: str = Query(..., min_length=1)):
    """Search downmagaz.net for matching magazines (same as the bot)."""
    try:
        results = await search_magazines(q)
    except Exception as e:
        logger.exception("Magazine search failed")
        raise HTTPException(status_code=502, detail=f"Search failed: {e}")
    return [
        {
            "edition_name": r["edition_name"],
            "tag_url": r["tag_url"],
            "version": r.get("version"),
            "countries": r.get("countries", []),
        }
        for r in results[:12]
    ]


@app.get("/api/magazines/editions")
async def api_magazine_editions(tag_url: str = Query(...), version: str | None = Query(None)):
    """List a magazine's available issues (version-filtered, newest first) — the
    same data the bot's /get magazine flow would page through."""
    try:
        posts = await scrape_magazine_tag(tag_url)
    except Exception as e:
        logger.exception("Magazine tag scrape failed")
        raise HTTPException(status_code=502, detail=f"Scrape failed: {e}")

    matching = [p for p in posts if matches_version(p["title"], version)]
    matching.sort(key=lambda p: p["date"], reverse=True)
    return [
        {"title": p["title"], "date": format_date(p["date"]), "url": p["url"]}
        for p in matching
    ]


@app.get("/api/magazines/links")
async def api_magazine_links(post_url: str = Query(...)):
    """Download links for one specific magazine issue (post)."""
    try:
        links = await get_download_links(post_url)
    except Exception as e:
        logger.exception("Magazine link scrape failed")
        raise HTTPException(status_code=502, detail=f"Scrape failed: {e}")
    return {"links": [{"domain": d, "href": h} for d, h in links]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
