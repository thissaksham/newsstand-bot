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

COUNTRIES = {
    "USA", "UK", "Europe", "Asia", "Middle East", "South Africa", "Australia", 
    "Canada", "New Zealand", "India", "Germany", "France", "Italy", "Spain", 
    "Singapore", "Philippines", "UK & US", "International"
}

def clean_version(tag_name: str, title_text: str) -> str:
    """Extracts the magazine version/edition from the post title."""
    pattern = re.compile(re.escape(tag_name), re.IGNORECASE)
    remains = pattern.sub("", title_text).strip()
    
    # Strip dates: DD.MM.YYYY, MM.DD.YYYY, DD.MM.YY, etc. (supporting 2-4 digit years)
    remains = re.sub(r'\d{1,2}[./-]\d{1,2}[./-]\d{2,4}', '', remains)
    # Strip month words and dates
    remains = re.sub(r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[ ,.-]*\d{1,2}[ ,.-]*\d{2,4}', '', remains, flags=re.IGNORECASE)
    remains = re.sub(r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[ ,.-]*\d{2,4}', '', remains, flags=re.IGNORECASE)
    # Strip stand-alone 2-4 digit numbers (e.g. years)
    remains = re.sub(r'\b\d{2,4}\b', '', remains)
    # Strip standalone 1-2 digit numbers (like month/issue numbers)
    remains = re.sub(r'\b\d{1,2}\b', '', remains)
    
    # Clean up hyphens, spaces, dots, and special chars
    remains = re.sub(r'[\s\-\–\—•.,/]+', ' ', remains).strip()
    return remains

def slugify(text: str) -> str:
    """Helper to convert text to a safe URL and filename slug."""
    cleaned = text.lower()
    cleaned = re.sub(r'[^a-z0-9\s-]', '', cleaned)
    return re.sub(r'[\s-]+', '-', cleaned).strip('-')

def get_magazine_tag_and_version(title_name: str, title_slug: str) -> tuple[str, str | None]:
    """Extracts the base tag name and version string from the DB title name and slug.
    If slug contains '--', it is versioned and we split it.
    """
    if "--" in title_slug:
        parts = title_slug.split("--", 1)
        version_slug = parts[1]
        num_words = len(version_slug.split("-"))
        name_words = title_name.split()
        version = " ".join(name_words[-num_words:])
        tag_name = " ".join(name_words[:-num_words])
        return tag_name, version
    return title_name, None

def matches_version(post_title: str, version: str | None) -> bool:
    """Checks if the scraped post title matches the target subscription version."""
    if not version:
        return True
    
    def normalize(t: str) -> str:
        t = t.lower()
        t = re.sub(r'[^a-z0-9]', ' ', t)
        return ' '.join(t.split())
        
    norm_title = normalize(post_title)
    norm_version = normalize(version)
    return bool(re.search(r'\b' + re.escape(norm_version) + r'\b', norm_title))

async def search_magazines(query: str) -> list[dict]:
    """Search downmagaz.net for magazines matching query.
    Returns list of dicts with edition details.
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
            
            editions_map = {}
            
            for s in stories:
                title_a = s.find(class_="stitle").find('a') if s.find(class_="stitle") else None
                if not title_a:
                    continue
                title_text = title_a.get_text(strip=True)
                
                mlink = s.find(class_="mlink")
                if mlink:
                    tag_links = [a for a in mlink.find_all('a', href=True) if '/tags/' in a['href']]
                    
                    story_countries = []
                    story_magazines = []
                    for tl in tag_links:
                        t_name = tl.get_text(strip=True)
                        t_url = tl['href']
                        if t_url.startswith("/"):
                            t_url = "https://downmagaz.net" + t_url
                            
                        # Separate country tags from magazine tags
                        if t_name.lower() in [c.lower() for c in COUNTRIES]:
                            story_countries.append(t_name)
                        else:
                            story_magazines.append((t_name, t_url))
                            
                    for m_name, m_url in story_magazines:
                        version = clean_version(m_name, title_text)
                        
                        # Fuzzy match the magazine tag name to the query
                        ratio = fuzz.ratio(query.lower(), m_name.lower())
                        pratio = fuzz.partial_ratio(query.lower(), m_name.lower())
                        token_sort = fuzz.token_sort_ratio(query.lower(), m_name.lower())
                        
                        if pratio > 70 or token_sort > 60 or ratio > 60:
                            edition_name = f"{m_name} {version}".strip() if version else m_name
                            key = edition_name.lower()
                            
                            # Generate safe slug
                            tag_slug = slugify(m_name)
                            if version:
                                version_slug = slugify(version)
                                slug = f"mag-{tag_slug}--{version_slug}"
                            else:
                                slug = f"mag-{tag_slug}"
                                
                            if key not in editions_map:
                                editions_map[key] = {
                                    "edition_name": edition_name,
                                    "tag_name": m_name,
                                    "tag_url": m_url,
                                    "slug": slug,
                                    "version": version,
                                    "countries": set(story_countries),
                                    "score": max(ratio, token_sort)
                                }
                            else:
                                editions_map[key]["countries"].update(story_countries)
            
            # Convert map to sorted list and format countries
            editions = []
            for item in editions_map.values():
                item["countries"] = sorted(list(item["countries"]))
                editions.append(item)
                
            # Sort by score descending
            editions.sort(key=lambda x: x["score"], reverse=True)
            # Remove score key before returning
            for e in editions:
                e.pop("score", None)
            return editions
            
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
