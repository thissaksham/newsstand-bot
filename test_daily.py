import os
import asyncio
from dotenv import load_dotenv
from telegram import Bot
from config.parser import Config
from scrapers import dailyepaper_in

async def main():
    load_dotenv()
    bot = Bot(os.getenv("BOT_TOKEN"))
    channel_id = os.getenv("STORAGE_CHANNEL_ID")
    config = Config()
    
    # Filter config for dailyepaper_in
    daily_titles = [t for t in config.titles if getattr(t, "scrape_website", None) == "dailyepaper_in"]
    print(f"Found {len(daily_titles)} dailyepaper titles in config...")
    
    for title in daily_titles:
        print(f"\n--- Testing {title.name} ---")
        try:
            res = await dailyepaper_in.scrape(title.source_url, title.slug, title.name)
            if not res:
                print(f"[{title.name}] Scraper returned None!")
                continue
                
            pdf_path, date_obj = res
            print(f"[{title.name}] Success! Extracted date {date_obj}.")
            
            # We don't bother logging to DB, just upload to Telegram to verify it works
            with open(pdf_path, 'rb') as f:
                await bot.send_document(
                    chat_id=channel_id,
                    document=f,
                    caption=f"🧪 <b>TEST UPLOAD</b>\n📰 <b>{title.name}</b>\n📅 {date_obj}",
                    parse_mode="HTML"
                )
            print(f"[{title.name}] Uploaded to Telegram Channel!")
            
        except Exception as e:
            print(f"[{title.name}] Crash: {e}")

if __name__ == "__main__":
    asyncio.run(main())
