import os
import re
import httpx
import gdown
from bs4 import BeautifulSoup
from datetime import datetime, date
from utils.helpers import get_today

async def scrape(source_url: str, slug: str, name: str) -> tuple[str, date] | None:
    """
    Scrapes the careerswave.in website for a given newspaper.
    Returns the absolute path to the downloaded PDF, or None if failed.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    print(f"[{name}] Fetching {source_url}...")
    
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=60.0) as client:
        try:
            resp = await client.get(source_url)
            if resp.status_code != 200:
                print(f"[{name}] Failed: HTTP {resp.status_code}")
                return None
                
            soup = BeautifulSoup(resp.text, 'html.parser')
            today_date = get_today()
            
            target_drive_url = None
            newspaper_date = None
            
            for a in soup.find_all('a', href=True):
                if 'drive.google.com/file/d/' in a['href']:
                    parent_text = a.parent.get_text(strip=True).lower()
                    
                    # Flexible date checking
                    nums = set(re.findall(r'\d+', parent_text))
                    has_day = str(today_date.day) in nums or f"{today_date.day:02d}" in nums
                    has_month_text = today_date.strftime('%b').lower() in parent_text or today_date.strftime('%B').lower() in parent_text
                    has_month_num = str(today_date.month) in nums or f"{today_date.month:02d}" in nums
                    
                    if has_day and (has_month_text or has_month_num):
                        target_drive_url = a['href']
                        newspaper_date = today_date
                        break
                        
            if not target_drive_url:
                print(f"[{name}] Failed: Today's edition ({today_date.strftime('%d %b %Y')}) not found on website yet")
                return None
                
            match = re.search(r'/d/([a-zA-Z0-9_-]+)', target_drive_url)
            if not match:
                return None
                
            file_id = match.group(1)
            
            output_file = f"{slug}_{newspaper_date}.pdf"
            
            print(f"[{name}] Downloading via gdown...")
            # Run gdown blocking call in thread to avoid blocking asyncio loop
            import asyncio
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: gdown.download(id=file_id, output=output_file, quiet=False))
            
            if not os.path.exists(output_file):
                print(f"[{name}] Failed: gdown output not found")
                return None
                
            return os.path.abspath(output_file), newspaper_date
            
        except Exception as e:
            print(f"[{name}] Error: {e}")
            return None
