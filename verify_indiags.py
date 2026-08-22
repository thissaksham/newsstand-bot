"""
Standalone verification for the indiags.com premium newspaper flow.

Run this anywhere with Python + dependencies (httpx, beautifulsoup4):

    pip install httpx beautifulsoup4
    python verify_indiags.py [The Hindu | Indian Express]

It will:
1. Fetch the listing page and save it as /tmp/indiags_listing.html
2. Parse the newspaper cards and print them
3. Find the requested paper and its /epaper/books/<id> link
4. Fetch /epaper/open/<id> with a cookie-aware session and save as /tmp/indiags_open_1.html
5. Wait 20 seconds
6. Fetch the same page again with the SAME session and save as /tmp/indiags_open_2.html
7. Search both pages for /go/ links using multiple strategies
8. If a /go/ link is found, try to download the PDF and save it as /tmp/indiags_<paper>.pdf
9. Print a summary of every step

No Telegram, no database, no bot logic.
"""

import asyncio
import re
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

BASE_URL = "https://www.indiags.com"
LISTING_URL = "https://www.indiags.com/epaper-pdf-download"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.google.com/",
}

OPEN_HEADERS = {
    **HEADERS,
    "Referer": "https://www.indiags.com/epaper-pdf-download",
}

PDF_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "application/pdf,*/*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.indiags.com/epaper/open/",
}


def normalise(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip()).lower()


def title_matches(card_name: str, target: str) -> bool:
    card = normalise(card_name)
    target = normalise(target)
    if target in card or card in target:
        return True
    target_words = set(target.split())
    card_words = set(card.split())
    return bool(target_words and target_words <= card_words)


def extract_book_id(url: str) -> str | None:
    m = re.search(r"/epaper/books/(\d+)", url)
    return m.group(1) if m else None


def extract_go_link_from_url(url: str) -> str | None:
    """Extract /go/<token> from a URL fragment like #unlock=<encoded>&exp=<ts>."""
    fragment = urllib.parse.urlparse(url).fragment
    if not fragment or not fragment.startswith("unlock="):
        return None
    raw = fragment[len("unlock="):]
    exp_idx = raw.rfind("&exp=")
    if exp_idx != -1:
        raw = raw[:exp_idx]
    try:
        decoded = urllib.parse.unquote(raw)
    except Exception:
        return None
    decoded = decoded.strip()
    if not decoded:
        return None
    if decoded.startswith("/go/"):
        return f"{BASE_URL}{decoded}"
    if decoded.startswith("http://") or decoded.startswith("https://"):
        return decoded
    if re.match(r"^[A-Za-z0-9_-]+$", decoded):
        return f"{BASE_URL}/go/{decoded}"
    return None


def find_go_links(html: str, label: str) -> list[str]:
    """Return every /go/ URL found in html, using multiple strategies."""
    found: list[str] = []
    soup = BeautifulSoup(html, "html.parser")

    # Strategy 1: inside #pdfUnlockBanner
    banner = soup.find("div", id="pdfUnlockBanner")
    if banner:
        for a in banner.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith("/go/"):
                found.append(f"{BASE_URL}{href}")
            elif "/go/" in href and href.startswith("http"):
                found.append(href)

    # Strategy 2: any anchor
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("/go/"):
            found.append(f"{BASE_URL}{href}")
        elif "/go/" in href and href.startswith("http"):
            found.append(href)

    # Strategy 3: raw text/script
    for match in re.finditer(r"https?://[^\"'<>\s]*?/go/[A-Za-z0-9]+", str(soup)):
        found.append(match.group(0))

    # dedupe preserving order
    seen = set()
    unique = []
    for u in found:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def save(path: Path, data: str | bytes) -> None:
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    print(f"  saved {path} ({len(data):,} bytes)")


async def download_pdf(url: str) -> bytes | None:
    print(f"  downloading PDF from {url} ...")
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=120.0, headers=PDF_HEADERS
        ) as client:
            async with client.stream("GET", url) as resp:
                print(f"  response: HTTP {resp.status_code}, content-type={resp.headers.get('content-type')}")
                resp.raise_for_status()
                chunks = []
                total = 0
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    total += len(chunk)
                    if total > 50 * 1024 * 1024:
                        print("  ABORTED: file larger than 50 MB")
                        return None
                    chunks.append(chunk)
                body = b"".join(chunks)
                print(f"  downloaded {len(body):,} bytes ({len(body)/1024/1024:.2f} MB)")
                return body
    except Exception as e:
        print(f"  download failed: {e}")
        return None


async def main(paper_name: str):
    out_dir = Path("/tmp")
    print("=" * 70)
    print(f"indiags.com verification for: {paper_name}")
    print(f"started at: {datetime.now().isoformat()}")
    print("=" * 70)

    # Step 1: listing page
    print(f"\n[1/5] Fetching listing page: {LISTING_URL}")
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30.0) as client:
        resp = await client.get(LISTING_URL)
        print(f"  HTTP {resp.status_code}, {len(resp.text):,} bytes")
        listing_html = resp.text
    save(out_dir / "indiags_listing.html", listing_html)

    # Step 2: parse cards
    print(f"\n[2/5] Parsing newspaper cards")
    soup = BeautifulSoup(listing_html, "html.parser")
    grid = soup.find("div", class_="ep-grid") or soup
    cards = []
    for card in grid.find_all("div", class_=True):
        foot = card.find("div", class_="foot")
        if not foot:
            continue
        a = foot.find("a", href=True)
        if not a:
            continue
        books_url = a["href"].strip()
        if books_url.startswith("/"):
            books_url = f"{BASE_URL}{books_url}"
        book_id = extract_book_id(books_url)
        if not book_id:
            continue
        title_el = card.find(class_="title") or card.find(class_="ep-title") or card.find("h3") or card.find("h4")
        name = title_el.get_text(strip=True) if title_el else card.get_text(" ", strip=True)
        cards.append({"name": name, "books_url": books_url, "book_id": book_id})
        print(f"  - {name:30s} book_id={book_id:6s} {books_url}")

    if not cards:
        print("  ERROR: no cards found")
        return 1

    # Step 3: match paper
    print(f"\n[3/5] Matching '{paper_name}'")
    paper = next((c for c in cards if title_matches(c["name"], paper_name)), None)
    if not paper:
        print(f"  ERROR: '{paper_name}' not found in cards")
        return 1
    print(f"  matched: {paper['name']} (book_id={paper['book_id']})")

    open_url = f"{BASE_URL}/epaper/open/{paper['book_id']}"
    print(f"\n[4/5] Fetching unlock page with cookie session: {open_url}")

    links1: list[str] = []
    links2: list[str] = []
    all_links: list[str] = []

    async with httpx.AsyncClient(
        headers=OPEN_HEADERS, follow_redirects=True, timeout=30.0
    ) as client:
        # Capture the redirect Location header without following it.
        resp_redirect = await client.get(open_url, follow_redirects=False)
        location = resp_redirect.headers.get("location", "")
        print(f"  redirect response: HTTP {resp_redirect.status_code}")
        if location:
            print(f"  Location header: {location}")
            loc_link = extract_go_link_from_url(location)
            if loc_link:
                print(f"  -> extracted /go/ link from Location: {loc_link}")
                all_links.append(loc_link)

        # Now follow redirects and inspect the final URL + HTML.
        resp1 = await client.get(open_url)
        final_url1 = str(resp1.url)
        print(f"  first fetch:  HTTP {resp1.status_code}, final_url={final_url1}, cookies={list(client.cookies.jar)}")
        url_link1 = extract_go_link_from_url(final_url1)
        if url_link1:
            print(f"  -> extracted /go/ link from final URL: {url_link1}")
            all_links.append(url_link1)

        html1 = resp1.text
        save(out_dir / "indiags_open_1.html", html1)
        links1 = find_go_links(html1, "first")
        print(f"  /go/ links found in first HTML: {links1 if links1 else 'none'}")

        # The link is already available in the redirect Location fragment, so we
        # only wait/re-fetch as a fallback for diagnostics.
        if all_links:
            print(f"  link already found from URL fragment; skipping 20s wait")
            links2 = []
        else:
            # wait
            print(f"  waiting 20 seconds...")
            await asyncio.sleep(20)

            # second fetch
            resp2 = await client.get(open_url)
            final_url2 = str(resp2.url)
            print(f"  second fetch: HTTP {resp2.status_code}, final_url={final_url2}, cookies={list(client.cookies.jar)}")
            url_link2 = extract_go_link_from_url(final_url2)
            if url_link2:
                print(f"  -> extracted /go/ link from second final URL: {url_link2}")
                all_links.append(url_link2)

            html2 = resp2.text
            save(out_dir / "indiags_open_2.html", html2)
            links2 = find_go_links(html2, "second")
            print(f"  /go/ links found in second HTML: {links2 if links2 else 'none'}")

    all_links = list(dict.fromkeys(all_links + links1 + links2))
    if not all_links:
        print("\n[5/5] RESULT: no /go/ link found in either fetch.")
        print("  Check saved files:")
        print("    /tmp/indiags_listing.html")
        print("    /tmp/indiags_open_1.html")
        print("    /tmp/indiags_open_2.html")
        return 1

    # Prefer the link extracted from the redirect Location header; it is the
    # first one issued by the server and is valid immediately (no timer needed).
    go_url = all_links[0]
    print(f"\n[5/5] Using /go/ link: {go_url}")
    pdf_bytes = await download_pdf(go_url)
    if pdf_bytes:
        is_pdf = pdf_bytes[:4] == b"%PDF"
        print(f"  starts with %PDF: {is_pdf}")
        if is_pdf:
            pdf_path = out_dir / f"indiags_{normalise(paper_name).replace(' ', '_')}.pdf"
            save(pdf_path, pdf_bytes)
            print(f"\nSUCCESS: PDF saved to {pdf_path}")
            return 0
        else:
            print(f"  first 200 bytes: {pdf_bytes[:200]!r}")
    print("\nRESULT: /go/ link found but PDF download failed or was not a PDF.")
    return 1


if __name__ == "__main__":
    paper = sys.argv[1] if len(sys.argv) > 1 else "Indian Express"
    rc = asyncio.run(main(paper))
    sys.exit(rc)
