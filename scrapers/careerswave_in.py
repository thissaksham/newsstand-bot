import os
import re
import asyncio
import httpx
import gdown
from bs4 import BeautifulSoup
from datetime import date
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
            
            # 1. Search for standard <a> tags with Google Drive links
            for a in soup.find_all('a', href=True):
                if 'drive.google.com/file/d/' in a['href']:
                    parent_text = a.parent.get_text(strip=True).lower()
                    
                    # Flexible date checking
                    nums = set(re.findall(r'\d+', parent_text))
                    has_day = str(today_date.day) in nums or f"{today_date.day:02d}" in nums
                    has_month_text = today_date.strftime('%b').lower() in parent_text or today_date.strftime('%B').lower() in parent_text
                    has_month_num = str(today_date.month) in nums or f"{today_date.month:02d}" in nums
                    has_year = str(today_date.year) in nums
                    
                    if has_day and (has_month_text or has_month_num) and has_year:
                        target_drive_url = a['href']
                        newspaper_date = today_date
                        break
                        
            # 2. Search for Ninja Tables or raw cells with Google Drive links if not found yet
            if not target_drive_url:
                for tr in soup.find_all('tr'):
                    tds = tr.find_all('td')
                    if len(tds) >= 2:
                        drive_url = None
                        date_cell_text = ""
                        for td in tds:
                            text = td.get_text(strip=True)
                            if 'drive.google.com/file/d/' in text:
                                drive_url = text
                            elif td.find('a', href=True) and 'drive.google.com/file/d/' in td.find('a', href=True).get('href', ''):
                                drive_url = td.find('a', href=True)['href']
                            else:
                                date_cell_text += " " + text.lower()
                                
                        if drive_url:
                            # Run the same flexible date checking on non-link cells in the row
                            nums = set(re.findall(r'\d+', date_cell_text))
                            has_day = str(today_date.day) in nums or f"{today_date.day:02d}" in nums
                            has_month_text = today_date.strftime('%b').lower() in date_cell_text or today_date.strftime('%B').lower() in date_cell_text
                            has_month_num = str(today_date.month) in nums or f"{today_date.month:02d}" in nums
                            has_year = str(today_date.year) in nums
                            
                            if has_day and (has_month_text or has_month_num) and has_year:
                                target_drive_url = drive_url
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
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: gdown.download(id=file_id, output=output_file, quiet=False))
            
            if not os.path.exists(output_file):
                print(f"[{name}] Failed: gdown output not found")
                return None
            
            if os.path.getsize(output_file) <= 1000:
                print(f"[{name}] Failed: downloaded file too small ({os.path.getsize(output_file)} bytes), likely an error page")
                os.remove(output_file)
                return None
            
            with open(output_file, 'rb') as f:
                magic = f.read(4)
            if magic != b'%PDF':
                print(f"[{name}] Failed: downloaded file is not a valid PDF (magic bytes: {magic!r})")
                os.remove(output_file)
                return None
                
            return os.path.abspath(output_file), newspaper_date
            
        except Exception as e:
            print(f"[{name}] Error: {e}")
            return None
