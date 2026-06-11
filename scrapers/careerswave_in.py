import os
import re
import httpx
import gdown

async def scrape(source_url: str, slug: str, name: str) -> str | None:
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
                
            drive_links = re.findall(r'https://drive\.google\.com/file/d/[a-zA-Z0-9_-]+', resp.text)
            if not drive_links:
                print(f"[{name}] Failed: No Drive links found")
                return None
                
            target_drive_url = drive_links[0]
            match = re.search(r'/d/([a-zA-Z0-9_-]+)', target_drive_url)
            if not match:
                return None
                
            file_id = match.group(1)
            output_file = f"{slug}.pdf"
            
            print(f"[{name}] Downloading via gdown...")
            # Run gdown blocking call in thread to avoid blocking asyncio loop
            import asyncio
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: gdown.download(id=file_id, output=output_file, quiet=False))
            
            if not os.path.exists(output_file):
                print(f"[{name}] Failed: gdown output not found")
                return None
                
            return os.path.abspath(output_file)
            
        except Exception as e:
            print(f"[{name}] Error: {e}")
            return None
