import os
import re
import urllib.request
import gdown
from bs4 import BeautifulSoup
from datetime import datetime, date
from utils.helpers import get_today

async def scrape(source_url: str, slug: str, name: str) -> tuple[str, date] | None:
    """
    Scrapes the dailyepaper.in website for a given newspaper.
    Returns the absolute path to the downloaded PDF and its date, or None if failed.
    """
    print(f"[{name}] Fetching {source_url}...")
    
    try:
        req = urllib.request.Request(
            source_url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/114.0.0.0 Safari/537.36'}
        )
        
        # Run urllib in executor to avoid blocking the event loop
        import asyncio
        loop = asyncio.get_running_loop()
        def fetch_html():
            with urllib.request.urlopen(req, timeout=60) as response:
                return response.read().decode('utf-8')
                
        html = await loop.run_in_executor(None, fetch_html)
        
        soup = BeautifulSoup(html, 'html.parser')
        
        today_date = get_today()
        target_a = None
        newspaper_date = None
        
        for a in soup.find_all('a', href=True):
            if 'drive.google.com/file/d/' in a['href']:
                parent_text = a.parent.get_text(strip=True)
                date_match = re.search(r'(\d{1,2}\s+[A-Za-z]+\s+\d{4})', parent_text)
                
                if date_match:
                    date_str = date_match.group(1)
                    try:
                        link_date = datetime.strptime(date_str, "%d %b %Y").date()
                        if link_date == today_date:
                            target_a = a
                            newspaper_date = link_date
                            break
                    except ValueError:
                        continue
                
        if not target_a:
            print(f"[{name}] Failed: Today's edition ({today_date.strftime('%d %b %Y')}) not found on website yet")
            return None
            
        target_drive_url = target_a['href']
        match = re.search(r'/d/([a-zA-Z0-9_-]+)', target_drive_url)
        if not match:
            return None
            
        file_id = match.group(1)
                
        output_file = f"{slug}_{newspaper_date}.pdf"
        
        print(f"[{name}] Downloading via gdown...")
        await loop.run_in_executor(None, lambda: gdown.download(id=file_id, output=output_file, quiet=False))
        
        if not os.path.exists(output_file):
            print(f"[{name}] Failed: gdown output not found")
            return None
            
        return os.path.abspath(output_file), newspaper_date
        
    except Exception as e:
        print(f"[{name}] Error: {e}")
        return None
