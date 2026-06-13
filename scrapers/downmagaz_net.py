"""
Scraper for downmagaz.net
Provides searching, tag page scraping, and post download link extraction.
"""

import httpx
from bs4 import BeautifulSoup
from urllib.parse import urlparse, quote
from thefuzz import fuzz
import re
from datetime import datetime, date

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
}

async def search_magazines(query: str) -> list[tuple[str, str]]:
    """Search downmagaz.net for magazines matching query.
    Returns list of (tag_name, tag_url) tuples.
    """
    search_url = "https://downmagaz.net/index.php?do=search"
    data = {
        "do": "search",
        "subaction": "search",
        "titleonly": "3",
        "story": query
    }
    
    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20.0) as client:
            resp = await client.post(search_url, data=data)
            if resp.status_code != 200:
                return []
                
            soup = BeautifulSoup(resp.text, 'html.parser')
            stories = soup.find_all(class_="story")
            
            tags = {}
            for s in stories:
                mlink = s.find(class_="mlink")
                if mlink:
                    tag_links = [a for a in mlink.find_all('a', href=True) if '/tags/' in a['href']]
                    for tl in tag_links:
                        tag_name = tl.get_text(strip=True)
                        href = tl['href']
                        if href.startswith("/"):
                            href = "https://downmagaz.net" + href
                        tags[tag_name] = href
            
            # Fuzzy match and filter tags
            matched_tags = []
            for tag_name, tag_url in tags.items():
                ratio = fuzz.ratio(query.lower(), tag_name.lower())
                pratio = fuzz.partial_ratio(query.lower(), tag_name.lower())
                token_sort = fuzz.token_sort_ratio(query.lower(), tag_name.lower())
                
                # Keep matches that have reasonable similarity
                if pratio > 70 or token_sort > 60 or ratio > 60:
                    matched_tags.append((tag_name, tag_url, max(ratio, token_sort)))
            
            # Sort by score descending
            matched_tags.sort(key=lambda x: x[2], reverse=True)
            return [(t[0], t[1]) for t in matched_tags]
            
    except Exception:
        return []

def parse_date_from_title(title_text: str) -> date | None:
    """Helper to parse a date from the post title."""
    # Pattern 1: DD.MM.YYYY or MM.DD.YYYY
    date_match = re.search(r'(\d{1,2})[.-](\d{1,2})[.-](\d{4})', title_text)
    if date_match:
        d1, d2, y = date_match.groups()
        try:
            if int(d1) > 12:
                return datetime.strptime(f"{d1}.{d2}.{y}", "%d.%m.%Y").date()
            else:
                if "audio" in title_text.lower():
                    return datetime.strptime(f"{d1}.{d2}.{y}", "%m.%d.%Y").date()
                else:
                    return datetime.strptime(f"{d1}.{d2}.{y}", "%d.%m.%Y").date()
        except Exception:
            pass
            
    # Pattern 2: Month name YYYY (e.g. June 2026, Jun 2026)
    month_match = re.search(
        r'(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)[a-z]*[ ,.-]*(\d{4})',
        title_text.lower()
    )
    if month_match:
        m_name, y_str = month_match.groups()
        for m_num in range(1, 13):
            m_fullname = datetime(2000, m_num, 1).strftime('%B').lower()
            m_shortname = datetime(2000, m_num, 1).strftime('%b').lower()
            if m_name == m_fullname or m_name == m_shortname:
                return date(int(y_str), m_num, 1)
                
    return None

async def scrape_magazine_tag(tag_url: str) -> list[dict]:
    """Scrapes the tag page on downmagaz.net to find recent posts.
    Returns list of dicts: {'title': str, 'url': str, 'date': date}
    """
    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20.0) as client:
            resp = await client.get(tag_url)
            if resp.status_code != 200:
                return []
                
            soup = BeautifulSoup(resp.text, 'html.parser')
            stories = soup.find_all(class_="story")
            
            results = []
            for s in stories:
                title_a = s.find(class_="stitle").find('a') if s.find(class_="stitle") else None
                if not title_a:
                    continue
                    
                title_text = title_a.get_text(strip=True)
                post_url = title_a['href']
                if post_url.startswith("/"):
                    post_url = "https://downmagaz.net" + post_url
                    
                parsed_date = parse_date_from_title(title_text)
                if not parsed_date:
                    parsed_date = date.today()
                    
                results.append({
                    "title": title_text,
                    "url": post_url,
                    "date": parsed_date
                })
            return results
    except Exception:
        return []

async def get_download_links(post_url: str) -> list[tuple[str, str]]:
    """Fetches post page and extracts external download links.
    Returns list of (domain, href).
    """
    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20.0) as client:
            resp = await client.get(post_url)
            if resp.status_code != 200:
                return []
                
            soup = BeautifulSoup(resp.text, 'html.parser')
            fullstory = soup.find(class_="fullstory")
            if not fullstory:
                return []
                
            links = []
            for a in fullstory.find_all('a', href=True):
                href = a['href']
                parsed = urlparse(href)
                domain = parsed.netloc.lower()
                
                # Check if it is an external link
                if domain and not domain.endswith("downmagaz.net") and not href.startswith("javascript:") and not href.startswith("#"):
                    links.append((domain, href))
            return links
    except Exception:
        return []
