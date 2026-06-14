import os
import re
import asyncio
import httpx
import gdown
import urllib.request
from bs4 import BeautifulSoup
from datetime import date, datetime, timedelta
from utils.helpers import get_today

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

DATE_RE  = re.compile(r'(\d{2}-\d{2}-\d{4})')          # DD-MM-YYYY anywhere in cell text
HREF_RE  = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)  # anchor href
BARE_RE  = re.compile(r'^\s*(https?://\S+)\s*$')         # bare URL, nothing else in cell
TD_RE    = re.compile(r'<td[^>]*>(.*?)</td>', re.IGNORECASE | re.DOTALL)

def fetch_page(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        encoding = resp.headers.get_content_charset("utf-8")
        return raw.decode(encoding, errors="replace")

def _clean_url(url: str) -> str:
    return url.strip().replace("&amp;", "&").replace("\\_", "_")

def parse_links(html: str) -> dict[str, str]:
    cells = TD_RE.findall(html)
    links: dict[str, str] = {}

    i = 0
    while i < len(cells) - 1:
        date_match = DATE_RE.search(cells[i])
        if date_match:
            date_str  = date_match.group(1)
            link_cell = cells[i + 1]

            # Priority 1: anchor href (Format B)
            url_match = HREF_RE.search(link_cell)
            if url_match:
                url = _clean_url(url_match.group(1))
                if url.startswith("https://drive.google.com") or "savefrom" in url:
                    links[date_str] = url
                elif not url.startswith("https://www.careerswave.in"):
                    links[date_str] = url
            else:
                # Priority 2: bare URL (Format A)
                bare_match = BARE_RE.match(link_cell)
                if bare_match:
                    links[date_str] = _clean_url(bare_match.group(1))

            i += 2   # consumed date cell + link cell
        else:
            i += 1

    return links

async def download_from_gdrive(file_id: str, output_file: str, name: str) -> bool:
    """Downloads a file from Google Drive using direct HTTP GET with confirmation bypass,
    falling back to gdown if it fails.
    """
    url = "https://docs.google.com/uc?export=download"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    }
    
    print(f"[{name}] Attempting direct HTTP download from Google Drive...")
    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=120.0) as client:
            resp = await client.get(url, params={"id": file_id})
            
            token = None
            for cookie_name, cookie_val in resp.cookies.items():
                if cookie_name.startswith("download_warning"):
                    token = cookie_val
                    break
                    
            if token:
                print(f"[{name}] Large file warning received. Confirming download...")
                resp = await client.get(url, params={"id": file_id, "confirm": token})
                
            if resp.status_code == 200 and resp.content.startswith(b"%PDF"):
                with open(output_file, "wb") as f:
                    f.write(resp.content)
                print(f"[{name}] Direct HTTP download succeeded ({len(resp.content)} bytes).")
                return True
            else:
                print(f"[{name}] Direct download did not return a valid PDF (status: {resp.status_code}).")
    except Exception as e:
        print(f"[{name}] Direct HTTP download failed: {e}")
        
    print(f"[{name}] Falling back to gdown download...")
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: gdown.download(id=file_id, output=output_file, quiet=True))
        if os.path.exists(output_file) and os.path.getsize(output_file) > 1000:
            with open(output_file, "rb") as f:
                magic = f.read(4)
            if magic == b"%PDF":
                print(f"[{name}] gdown fallback download succeeded.")
                return True
    except Exception as e:
        print(f"[{name}] gdown fallback download failed: {e}")
        
    return False

async def scrape(source_url: str, slug: str, name: str, target_date: date = None) -> tuple[str, date] | None:
    """
    Scrapes the careerswave.in website for a given newspaper using careerswave_scraper.py logic.
    Returns the absolute path to the downloaded PDF and the date, or None if failed.
    """
    print(f"[{name}] Fetching {source_url}...")
    try:
        loop = asyncio.get_running_loop()
        html = await loop.run_in_executor(None, lambda: fetch_page(source_url))
    except Exception as e:
        print(f"[{name}] Failed to fetch page: {e}")
        return None

    links = parse_links(html)
    if not links:
        print(f"[{name}] No dated links found on the page.")
        return None

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
                print(f"[{name}] Could not extract Google Drive file ID from {view_url}")
                continue
                
            file_id = match.group(1)
            output_file = f"{slug}_{d.strftime('%Y-%m-%d')}.pdf"
            
            if not await download_from_gdrive(file_id, output_file, name):
                print(f"[{name}] Failed to download PDF for {date_str}")
                continue
                
            return os.path.abspath(output_file), d

    print(f"[{name}] No edition found for any of the dates: {[d.strftime('%Y-%m-%d') for d in dates_to_try]}")
    return None
