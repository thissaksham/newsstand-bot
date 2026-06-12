import os
import asyncio
import importlib
from telegram import Bot
from telegram.error import RetryAfter
from dotenv import load_dotenv

from config import Config
from database.operations import (
    add_edition, update_edition_status, get_pending_scrapes,
    get_subscribers_for_title, log_delivery, has_been_delivered,
    upsert_scrape_status, get_failed_scrapes, _get_client
)
from utils.helpers import get_today, format_date
from datetime import datetime, date, timezone

async def deliver_to_subscribers(bot: Bot, edition_id: int, file_id: str, title_id: int, title_name: str, newspaper_date: date):
    """Deliver the edition to all subscribed users."""
    subscribers = await get_subscribers_for_title("", title_id)
    print(f"[{title_name}] Found {len(subscribers)} subscribers for delivery.")
    
    friendly_date = format_date(newspaper_date)
    for user_id in subscribers:
        if await has_been_delivered("", user_id, edition_id):
            continue
            
        try:
            await bot.send_document(
                chat_id=user_id,
                document=file_id,
                caption=f"📰 Here is your **{title_name}** for {friendly_date}!",
                parse_mode="Markdown"
            )
            await log_delivery("", user_id, edition_id, "success")
            print(f"[{title_name}] Delivered to {user_id}")
        except RetryAfter as e:
            print(f"Rate limited! Sleeping for {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            await bot.send_document(
                chat_id=user_id,
                document=file_id,
                caption=f"📰 Here is your **{title_name}** for {friendly_date}!",
                parse_mode="Markdown"
            )
            await log_delivery("", user_id, edition_id, "success")
        except Exception as e:
            print(f"[{title_name}] Failed to deliver to {user_id}: {e}")
            await log_delivery("", user_id, edition_id, "failed")
            
        await asyncio.sleep(0.1)

async def catch_up_deliveries(bot: Bot, scrape_date: date):
    """Deliver today's editions to any subscribers who haven't received them yet."""
    db = await _get_client()
    
    # 1. Fetch all active subscriptions
    subs_resp = await db.table("subscriptions").select("user_id, title_id").execute()
    if not subs_resp.data:
        return
        
    # 2. Get all editions for today
    editions_resp = await db.table("editions").select("id, title_id, file_id, titles(name)").eq("date", scrape_date.isoformat()).execute()
    if not editions_resp.data:
        return
        
    # Map title_id to edition info
    editions_map = {}
    for row in editions_resp.data:
        if row.get("file_id"):
            editions_map[row["title_id"]] = {
                "edition_id": row["id"],
                "file_id": row["file_id"],
                "title_name": row["titles"]["name"] if row.get("titles") else f"Title #{row['title_id']}"
            }
    
    # 3. Check each subscription
    for sub in subs_resp.data:
        user_id = sub["user_id"]
        title_id = sub["title_id"]
        
        if title_id in editions_map:
            ed = editions_map[title_id]
            edition_id = ed["edition_id"]
            file_id = ed["file_id"]
            title_name = ed["title_name"]
            
            # Check if already delivered
            if not await has_been_delivered("", user_id, edition_id):
                friendly_date = format_date(scrape_date)
                print(f"[Catch-up] Delivering {title_name} to {user_id}...")
                try:
                    await bot.send_document(
                        chat_id=user_id,
                        document=file_id,
                        caption=f"📰 Here is your **{title_name}** for {friendly_date}!",
                        parse_mode="Markdown"
                    )
                    await log_delivery("", user_id, edition_id, "success")
                    print(f"[Catch-up] Delivered to {user_id}")
                except RetryAfter as e:
                    print(f"[Catch-up] Rate limited! Sleeping for {e.retry_after}s")
                    await asyncio.sleep(e.retry_after)
                    try:
                        await bot.send_document(
                            chat_id=user_id,
                            document=file_id,
                            caption=f"📰 Here is your **{title_name}** for {friendly_date}!",
                            parse_mode="Markdown"
                        )
                        await log_delivery("", user_id, edition_id, "success")
                    except Exception as ex:
                        print(f"[Catch-up] Retry failed: {ex}")
                except Exception as e:
                    print(f"[Catch-up] Failed to deliver: {e}")
                    await log_delivery("", user_id, edition_id, "failed")
                
                await asyncio.sleep(0.2)

async def main():
    load_dotenv()
    bot_token = os.getenv("BOT_TOKEN")
    channel_id = os.getenv("STORAGE_CHANNEL_ID")
    
    if not bot_token or not channel_id:
        print("Missing BOT_TOKEN or STORAGE_CHANNEL_ID in .env")
        return

    config = Config.get()
    today = get_today()
    bot = Bot(token=bot_token)
    
    print(f"Scraping all active titles for today: {today}")
    
    # 1. Get titles that haven't been successfully scraped today (max 6 attempts)
    pending_titles = await get_pending_scrapes("", scrape_date=today, max_attempts=6)
    
    if not pending_titles:
        print("All active titles have already been scraped for today! Exiting.")
        return
        
    print(f"Found {len(pending_titles)} titles pending scrape.")
    
    # 2. Iterate and scrape using the dynamic source module
    for title in pending_titles:
        name = title["name"]
        slug = title["slug"]
        source_module_name = title.get("source")
        
        # We also need the source_url from the config.yaml to pass to the scraper
        conf_title = next((t for t in config.titles if getattr(t, "slug", "") == slug), None)
        source_url = getattr(conf_title, "source_url", None) if conf_title else None
        
        if not source_module_name or not source_url:
            print(f"[{name}] Skipped: No source module or source URL defined.")
            continue
            
        # Dynamically import the scraper module
        try:
            scraper_module = importlib.import_module(f"scrapers.{source_module_name}")
        except ImportError:
            print(f"[{name}] Failed: Scraper module 'scrapers.{source_module_name}' not found.")
            continue
            
        # Run the generic scrape() function
        result = await scraper_module.scrape(source_url, slug, name)
        
        if not result:
            # Failed to scrape, record the attempt
            await upsert_scrape_status("", title["id"], today, status="failed", increment_attempts=True)
            continue
            
        output_file, newspaper_date = result
        
        if not output_file or not os.path.exists(output_file):
            await upsert_scrape_status("", title["id"], today, status="failed", increment_attempts=True)
            continue

        # 3. Upload to Telegram Storage Channel
        friendly_date = format_date(newspaper_date)
        print(f"[{name}] Uploading to Telegram Channel...")
        try:
            with open(output_file, 'rb') as f:
                message = await bot.send_document(
                    chat_id=channel_id,
                    document=f,
                    caption=f"📰 **{name}** • {friendly_date}",
                    parse_mode="Markdown",
                    read_timeout=300,
                    write_timeout=300,
                    connect_timeout=60,
                    pool_timeout=60
                )
            
            telegram_file_id = message.document.file_id
            message_id = message.message_id
            
            # 4. Update Database
            edition_id = await add_edition(
                db_path="",
                title_id=title["id"],
                edition_date=newspaper_date,
                download_url=source_url,
                status="stored"
            )
            
            await update_edition_status(
                db_path="",
                edition_id=edition_id,
                status="delivered", 
                file_id=telegram_file_id,
                message_id=message_id
            )
            
            # Mark the scrape as successfully found!
            await upsert_scrape_status("", title["id"], today, status="found", increment_attempts=True)
            
            print(f"[{name}] Success! Database updated.")
            
            # 5. Deliver to Users
            await deliver_to_subscribers(bot, edition_id, telegram_file_id, title["id"], name, newspaper_date)
                
        except Exception as e:
            print(f"[{name}] Error during upload/delivery: {e}")
            
        finally:
            if os.path.exists(output_file):
                os.remove(output_file)

    # 5.5 Catch-up deliveries for any missed subscriptions
    await catch_up_deliveries(bot, today)

    # 6. Send Failure Report on the last run of the day (06:xx UTC -> 11:30 IST)
    if datetime.now(timezone.utc).hour == 6:
        failed_titles = await get_failed_scrapes("", today)
        if failed_titles:
            report = "⚠️ *Daily Scrape Failure Report*\n\nThe following newspapers could not be found today after 6 attempts:\n"
            for t in failed_titles:
                report += f"• {t}\n"
            try:
                await bot.send_message(chat_id=channel_id, text=report, parse_mode="Markdown")
                print("Failure report sent to channel.")
            except Exception as e:
                print(f"Failed to send report: {e}")

if __name__ == "__main__":
    asyncio.run(main())
