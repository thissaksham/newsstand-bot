"""
Standalone diagnostic for the indiags.com premium newspaper flow.

Run this in the same environment as the bot (so it has the same network access):

    python debug_indiags.py [The Hindu | Indian Express]

It will:
1. Fetch the listing page
2. Find the requested newspaper card and /epaper/books/<id> link
3. Open /epaper/open/<id>
4. Wait 20s for the unlock banner
5. Find the /go/<token> link
6. Download the PDF (or follow redirects to the real file)
7. Print response headers, final URL, file size, and content type
8. Save the PDF to /tmp if it looks valid

No Telegram, no database, no bot token needed.
"""

import asyncio
import sys
from datetime import datetime

from scrapers.indiags_com import list_papers, _fetch_open_page_link, _title_matches
from utils.helpers import download_url_to_bytes


async def main(paper_name: str):
    print(f"[{datetime.now().isoformat()}] Looking for '{paper_name}' on indiags.com...")
    print("-" * 60)

    papers = await list_papers()
    if not papers:
        print("ERROR: No newspapers found on the listing page.")
        return 1

    print(f"Found {len(papers)} newspaper card(s):")
    for p in papers:
        print(f"  - {p['name']:30s} -> {p['books_url']}")
    print("-" * 60)

    paper = None
    for p in papers:
        if _title_matches(p["name"], paper_name):
            paper = p
            break

    if not paper:
        print(f"ERROR: Could not match '{paper_name}' to any card.")
        return 1

    print(f"Matched card: {paper['name']}")
    print(f"Books URL:    {paper['books_url']}")
    print(f"Book ID:      {paper['book_id']}")
    print("-" * 60)

    print(f"[{datetime.now().isoformat()}] Opening unlock page...")
    try:
        go_link = await _fetch_open_page_link(paper["book_id"])
    except Exception as e:
        print(f"ERROR: Failed to get /go/ link: {e}")
        return 1

    print(f"Got /go/ link: {go_link}")
    print("-" * 60)

    print(f"[{datetime.now().isoformat()}] Downloading PDF...")
    pdf_bytes = await download_url_to_bytes(go_link, timeout=120.0)
    if not pdf_bytes:
        print("ERROR: download_url_to_bytes returned None.")
        print("Possible causes:")
        print("  - /go/ link expired before download started")
        print("  - Server returned non-2xx status")
        print("  - Response was too large")
        print("  - Network / TLS / headers blocked")
        return 1

    print(f"Downloaded {len(pdf_bytes):,} bytes ({len(pdf_bytes)/1024/1024:.2f} MB)")

    # Heuristic PDF validation
    is_pdf = pdf_bytes[:4] == b"%PDF"
    print(f"Starts with %PDF header: {is_pdf}")

    out_path = f"/tmp/indiags_{paper_name.replace(' ', '_').lower()}_{datetime.now().strftime('%H%M%S')}.pdf"
    if is_pdf:
        with open(out_path, "wb") as f:
            f.write(pdf_bytes)
        print(f"Saved to: {out_path}")
    else:
        print("WARNING: Download does not look like a PDF. First 200 bytes:")
        print(pdf_bytes[:200])

    print("-" * 60)
    print("Diagnostic complete.")
    return 0


if __name__ == "__main__":
    paper = sys.argv[1] if len(sys.argv) > 1 else "Indian Express"
    rc = asyncio.run(main(paper))
    sys.exit(rc)
