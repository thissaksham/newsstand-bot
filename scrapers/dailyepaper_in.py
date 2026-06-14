import os
import re
import asyncio
import urllib.request
import httpx
import gdown
from bs4 import BeautifulSoup
from datetime import date
from utils.helpers import get_today

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
            
            # Check for download warning cookie
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
        loop = asyncio.get_running_loop()
        def fetch_html():
            with urllib.request.urlopen(req, timeout=60) as response:
                return response.read().decode('utf-8')
                
        html = await loop.run_in_executor(None, fetch_html)
        
        soup = BeautifulSoup(html, 'html.parser')
        
        base_date = target_date or get_today()
        dates_to_try = [base_date]
        if not target_date:
            from datetime import timedelta
            dates_to_try.append(base_date - timedelta(days=1))
            dates_to_try.append(base_date - timedelta(days=2))
            dates_to_try.append(base_date - timedelta(days=3))
            
        target_drive_url = None
        newspaper_date = None
        
        for d in dates_to_try:
            # 1. Search for standard <a> tags with Google Drive links
            for a in soup.find_all('a', href=True):
                if 'drive.google.com/file/d/' in a['href']:
                    parent_text = a.parent.get_text(strip=True).lower()
                    
                    # Flexible date checking: handles '12 Jun', '12th June', '12-06-2026', etc.
                    nums = set(re.findall(r'\d+', parent_text))
                    has_day = str(d.day) in nums or f"{d.day:02d}" in nums
                    has_month_text = d.strftime('%b').lower() in parent_text or d.strftime('%B').lower() in parent_text
                    has_month_num = str(d.month) in nums or f"{d.month:02d}" in nums
                    has_year = str(d.year) in nums
                    
                    if has_day and (has_month_text or has_month_num) and has_year:
                        target_drive_url = a['href']
                        newspaper_date = d
                        break
                        
            if target_drive_url:
                break
                
            # 2. Search for Ninja Tables or raw cells with Google Drive links if not found yet
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
                        has_day = str(d.day) in nums or f"{d.day:02d}" in nums
                        has_month_text = d.strftime('%b').lower() in date_cell_text or d.strftime('%B').lower() in date_cell_text
                        has_month_num = str(d.month) in nums or f"{d.month:02d}" in nums
                        has_year = str(d.year) in nums
                        
                        if has_day and (has_month_text or has_month_num) and has_year:
                            target_drive_url = drive_url
                            newspaper_date = d
                            break
            if target_drive_url:
                break
                
        if not target_drive_url:
            print(f"[{name}] Failed: No edition found for any of the dates: {[d.strftime('%Y-%m-%d') for d in dates_to_try]}")
            return None
            
        match = re.search(r'/d/([a-zA-Z0-9_-]+)', target_drive_url)
        if not match:
            return None
            
        file_id = match.group(1)
                
        output_file = f"{slug}_{newspaper_date}.pdf"
        
        if not await download_from_gdrive(file_id, output_file, name):
            print(f"[{name}] Failed to download PDF from Google Drive")
            return None
            
        return os.path.abspath(output_file), newspaper_date
        
    except Exception as e:
        print(f"[{name}] Error: {e}")
        return None
