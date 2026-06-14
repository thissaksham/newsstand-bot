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
    upsert_scrape_status, get_failed_scrapes, _get_client,
    get_edition, add_title
)
from utils.helpers import get_today, format_date
from datetime import datetime, date, timezone, timedelta
from urllib.parse import quote
from scrapers.downmagaz_net import scrape_magazine_tag, get_download_links, get_magazine_tag_and_version, matches_version

def split_pdf_if_large(filepath: str, max_size_mb: float = 45.0) -> list[str]:
    """Splits a PDF file into multiple files if its size exceeds max_size_mb.
    Returns a list of file paths. If the file is small or not a PDF, returns [filepath].
    """
    import os
    if not filepath.endswith(".pdf") or not os.path.exists(filepath):
        return [filepath]
        
    file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
    if file_size_mb <= max_size_mb:
        return [filepath]
        
    print(f"File {filepath} is {file_size_mb:.2f} MB, which exceeds {max_size_mb} MB. Splitting...")
    try:
        from pypdf import PdfReader, PdfWriter
        
        reader = PdfReader(filepath)
        total_pages = len(reader.pages)
        
        num_parts = int(file_size_mb // max_size_mb) + 1
        pages_per_part = (total_pages // num_parts) + 1
        
        parts = []
        base, ext = os.path.splitext(filepath)
        
        for i in range(num_parts):
            start_page = i * pages_per_part
            end_page = min(start_page + pages_per_part, total_pages)
            if start_page >= total_pages:
                break
                
            writer = PdfWriter()
            for page_num in range(start_page, end_page):
                writer.add_page(reader.pages[page_num])
                
            part_path = f"{base}_part{i+1}{ext}"
            with open(part_path, "wb") as f:
                writer.write(f)
                
            part_size_mb = os.path.getsize(part_path) / (1024 * 1024)
            print(f"Created part {i+1}: {part_path} ({part_size_mb:.2f} MB, pages {start_page+1}-{end_page})")
            parts.append(part_path)
            
        return parts
    except Exception as e:
        print(f"Error splitting PDF: {e}")
        return [filepath]

async def process_magazine_title(bot: Bot, title: dict, today: date):
    """Scrapes recent editions from downmagaz.net tag, extracts links,
    and sends updates to subscribers if not already processed.
    """
    name = title["name"]
    title_id = title["id"]
    slug = title["slug"]
    
    tag_name, version = get_magazine_tag_and_version(name, slug)
    tag_url = f"https://downmagaz.net/tags/{quote(tag_name.lower())}/"
    
    print(f"[{name}] Scraping tag page {tag_url}...")
    posts = await scrape_magazine_tag(tag_url)
    
    if not posts:
        print(f"[{name}] No posts found on tag page.")
        await upsert_scrape_status("", title_id, today, status="failed", increment_attempts=True)
        return
        
    print(f"[{name}] Found {len(posts)} posts on tag page.")
    
    success_any = False
    
    for post in posts:
        post_title = post["title"]
        post_url = post["url"]
        edition_date = post["date"]
        
        # Check if the post matches the target version
        if not matches_version(post_title, version):
            continue
            
        edition = await get_edition("", title_id, edition_date)
        
        processed_urls = []
        if edition and edition.get("file_id"):
            processed_urls = edition["file_id"].split(",")
            
        if post_url in processed_urls:
            continue
            
        print(f"[{name}] Found post: {post_title} for edition date {edition_date}")
        
        # Only deliver to subscribers if the edition is new (published within last 3 days)
        # Old historical editions are stored in the DB but not sent as alerts
        is_new_edition = edition_date >= today - timedelta(days=3)
        
        links = await get_download_links(post_url)
        if not links:
            print(f"[{name}] No download links found for {post_title}. Skipping.")
            continue
            
        subscribers = await get_subscribers_for_title("", title_id)
        delivered_users = []
        if subscribers and is_new_edition:
            links_html = ""
            for domain, href in links:
                links_html += f"• <a href=\"{href}\">Download via {domain}</a>\n"
                
            msg_text = (
                f"📖 <b>New Magazine Alert!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"New edition of <b>{name}</b> is available:\n"
                f"👉 <b>{post_title}</b>\n\n"
                f"Download Links:\n{links_html}"
            )
            
            for user_id in subscribers:
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=msg_text,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                    delivered_users.append(user_id)
                    print(f"[{name}] Delivered to {user_id}")
                except Exception as e:
                    print(f"[{name}] Failed to deliver to {user_id}: {e}")
        elif not is_new_edition:
            print(f"[{name}] Skipping alert for old historical edition {post_title} (date: {edition_date}).")
                    
        # Update DB: mark this post_url as processed in edition
        edition_id = None
        if not edition:
            new_edition_id = await add_edition(
                db_path="",
                title_id=title_id,
                edition_date=edition_date,
                download_url=post_url,
                status="pending"
            )
            await update_edition_status(
                db_path="",
                edition_id=new_edition_id,
                status="delivered",
                file_id=post_url
            )
            edition_id = new_edition_id
        else:
            new_file_id = f"{edition.get('file_id') or ''},{post_url}".strip(",")
            await update_edition_status(
                db_path="",
                edition_id=edition["id"],
                status="delivered",
                file_id=new_file_id
            )
            edition_id = edition["id"]
            
        # Log delivery status for subscribers (only if it was a new edition that we attempted to deliver)
        if subscribers and is_new_edition:
            for user_id in subscribers:
                status = "success" if user_id in delivered_users else "failed"
                await log_delivery("", user_id, edition_id, status)
            
        success_any = True
        
    if success_any:
        await upsert_scrape_status("", title_id, today, status="found", increment_attempts=True)
    else:
        # tag page parsed successfully but no new posts found
        await upsert_scrape_status("", title_id, today, status="found", increment_attempts=True)

async def deliver_to_subscribers(bot: Bot, edition_id: int, file_id: str, title_id: int, title_name: str, newspaper_date: date):
    """Deliver the edition to all subscribed users."""
    subscribers = await get_subscribers_for_title("", title_id)
    print(f"[{title_name}] Found {len(subscribers)} subscribers for delivery.")
    
    friendly_date = format_date(newspaper_date)
    file_ids = file_id.split(",")
    
    for user_id in subscribers:
        if await has_been_delivered("", user_id, edition_id):
            continue
            
        try:
            for idx, fid in enumerate(file_ids):
                part_suffix = f" (Part {idx+1}/{len(file_ids)})" if len(file_ids) > 1 else ""
                await bot.send_document(
                    chat_id=user_id,
                    document=fid,
                    caption=f"📰 Here is your **{title_name}**{part_suffix} for {friendly_date}!",
                    parse_mode="Markdown"
                )
            await log_delivery("", user_id, edition_id, "success")
            print(f"[{title_name}] Delivered to {user_id}")
        except RetryAfter as e:
            print(f"Rate limited! Sleeping for {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            try:
                for idx, fid in enumerate(file_ids):
                    part_suffix = f" (Part {idx+1}/{len(file_ids)})" if len(file_ids) > 1 else ""
                    await bot.send_document(
                        chat_id=user_id,
                        document=fid,
                        caption=f"📰 Here is your **{title_name}**{part_suffix} for {friendly_date}!",
                        parse_mode="Markdown"
                    )
                await log_delivery("", user_id, edition_id, "success")
            except Exception as ex:
                print(f"[{title_name}] Retry failed: {ex}")
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
    editions_resp = await db.table("editions").select("id, title_id, file_id, titles(name, category)").eq("date", scrape_date.isoformat()).execute()
    if not editions_resp.data:
        return
        
    # Map title_id to edition info
    editions_map = {}
    for row in editions_resp.data:
        if row.get("file_id"):
            editions_map[row["title_id"]] = {
                "edition_id": row["id"],
                "file_id": row["file_id"],
                "title_name": row["titles"]["name"] if row.get("titles") else f"Title #{row['title_id']}",
                "category": row["titles"]["category"] if row.get("titles") else "Newspaper"
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
            category = ed.get("category", "Newspaper")
            
            # Check if already delivered
            if not await has_been_delivered("", user_id, edition_id):
                friendly_date = format_date(scrape_date)
                
                if category == "Magazine":
                    print(f"[Catch-up] Delivering magazine {title_name} to {user_id}...")
                    # file_id contains the post_url(s)
                    post_urls = file_id.split(",")
                    success_all = True
                    for post_url in post_urls:
                        links = await get_download_links(post_url)
                        if links:
                            links_html = ""
                            for domain, href in links:
                                links_html += f"• <a href=\"{href}\">Download via {domain}</a>\n"
                            msg_text = (
                                f"📖 <b>New Magazine Alert!</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                                f"New edition of <b>{title_name}</b> is available for <b>{friendly_date}</b>:\n\n"
                                f"Download Links:\n{links_html}"
                            )
                            try:
                                await bot.send_message(
                                    chat_id=user_id,
                                    text=msg_text,
                                    parse_mode="HTML",
                                    disable_web_page_preview=True
                                )
                            except Exception as e:
                                print(f"[Catch-up] Failed to deliver magazine alert to {user_id}: {e}")
                                success_all = False
                        else:
                            success_all = False
                    if success_all:
                        await log_delivery("", user_id, edition_id, "success")
                        print(f"[Catch-up] Delivered magazine {title_name} to {user_id}")
                    else:
                        await log_delivery("", user_id, edition_id, "failed")
                else:
                    print(f"[Catch-up] Delivering {title_name} to {user_id}...")
                    file_ids = file_id.split(",")
                    try:
                        for idx, fid in enumerate(file_ids):
                            part_suffix = f" (Part {idx+1}/{len(file_ids)})" if len(file_ids) > 1 else ""
                            await bot.send_document(
                                chat_id=user_id,
                                document=fid,
                                caption=f"📰 Here is your **{title_name}**{part_suffix} for {friendly_date}!",
                                parse_mode="Markdown"
                            )
                        await log_delivery("", user_id, edition_id, "success")
                        print(f"[Catch-up] Delivered to {user_id}")
                    except RetryAfter as e:
                        print(f"[Catch-up] Rate limited! Sleeping for {e.retry_after}s")
                        await asyncio.sleep(e.retry_after)
                        try:
                            for idx, fid in enumerate(file_ids):
                                part_suffix = f" (Part {idx+1}/{len(file_ids)})" if len(file_ids) > 1 else ""
                                await bot.send_document(
                                    chat_id=user_id,
                                    document=fid,
                                    caption=f"📰 Here is your **{title_name}**{part_suffix} for {friendly_date}!",
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
    
    # 1. Get titles that haven't been successfully scraped today (max 7 attempts)
    pending_titles = await get_pending_scrapes("", scrape_date=today, max_attempts=7)
    
    if not pending_titles:
        print("All active titles have already been scraped for today! Exiting.")
        return
        
    print(f"Found {len(pending_titles)} titles pending scrape.")
    
    # Filter titles based on category and current hour in IST (UTC+5:30)
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    current_hour = ist_now.hour
    
    # If GITHUB_EVENT_NAME is not set (local run) or is 'workflow_dispatch' (manual run), bypass hour filters
    github_event = os.getenv("GITHUB_EVENT_NAME")
    is_manual = (github_event is None) or (github_event == "workflow_dispatch")
    if is_manual:
        print("Manual or local run detected. Bypassing scheduling hour filters.")
        
    filtered_titles = []
    for title in pending_titles:
        category = title.get("category", "Newspaper")
        if is_manual:
            filtered_titles.append(title)
        elif category == "Newspaper":
            if current_hour >= 6:
                filtered_titles.append(title)
            else:
                print(f"[{title['name']}] Skipped: Newspaper checks only run after 6am IST (current hour: {current_hour} IST).")
        elif category == "Magazine":
            filtered_titles.append(title)
        else:
            filtered_titles.append(title)
            
    if not filtered_titles:
        print(f"No titles to scrape for the current hour ({current_hour} IST). Exiting.")
        return
        
    print(f"Found {len(filtered_titles)} titles to scrape in this run.")
    
    # 2. Iterate and scrape using the dynamic source module
    for title in filtered_titles:
        name = title["name"]
        slug = title["slug"]
        
        category = title.get("category", "Newspaper")
        if category == "Magazine":
            try:
                await process_magazine_title(bot, title, today)
            except Exception as e:
                print(f"[{name}] Error processing magazine: {e}")
            continue
        
        # Check if already scraped and uploaded today to prevent duplicate channel uploads
        existing_edition = await get_edition("", title["id"], today)
        if existing_edition and existing_edition.get("file_id") and existing_edition.get("status") == "delivered":
            print(f"[{name}] Already scraped and uploaded to channel today. Skipping upload.")
            await upsert_scrape_status("", title["id"], today, status="found", increment_attempts=False)
            await deliver_to_subscribers(bot, existing_edition["id"], existing_edition["file_id"], title["id"], name, today)
            continue
            
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
        file_parts = []
        try:
            # Split PDF if it is large (> 45 MB)
            file_parts = split_pdf_if_large(output_file, max_size_mb=45.0)
            
            telegram_file_ids = []
            message_ids = []
            
            for idx, part_file in enumerate(file_parts):
                part_suffix = f" (Part {idx+1}/{len(file_parts)})" if len(file_parts) > 1 else ""
                with open(part_file, 'rb') as f:
                    message = await bot.send_document(
                        chat_id=channel_id,
                        document=f,
                        caption=f"📰 **{name}**{part_suffix} • {friendly_date}",
                        parse_mode="Markdown",
                        read_timeout=300,
                        write_timeout=300,
                        connect_timeout=60,
                        pool_timeout=60
                    )
                telegram_file_ids.append(message.document.file_id)
                message_ids.append(str(message.message_id))
            
            combined_file_id = ",".join(telegram_file_ids)
            combined_message_id = ",".join(message_ids)
            
            # 4. Update Database
            edition_id = await add_edition(
                db_path="",
                title_id=title["id"],
                edition_date=newspaper_date,
                download_url=source_url,
                status="stored"
            )
            
            first_message_id = int(message_ids[0]) if message_ids else None
            
            await update_edition_status(
                db_path="",
                edition_id=edition_id,
                status="delivered", 
                file_id=combined_file_id,
                message_id=first_message_id
            )
            
            # Mark the scrape as successfully found!
            await upsert_scrape_status("", title["id"], today, status="found", increment_attempts=True)
            
            print(f"[{name}] Success! Database updated.")
            
            # 5. Deliver to Users
            await deliver_to_subscribers(bot, edition_id, combined_file_id, title["id"], name, newspaper_date)
                
        except Exception as e:
            print(f"[{name}] Error during upload/delivery: {e}")
            
        finally:
            # Clean up all created files
            for part_file in file_parts:
                try:
                    if os.path.exists(part_file):
                        os.remove(part_file)
                except Exception as e:
                    print(f"Failed to remove part file {part_file}: {e}")
            try:
                if os.path.exists(output_file):
                    os.remove(output_file)
            except Exception as e:
                print(f"Failed to remove output file {output_file}: {e}")

    # 5.5 Catch-up deliveries for any missed subscriptions
    await catch_up_deliveries(bot, today)

    # 6. Send Failure Report at 12pm IST (noon)
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    if ist_now.hour == 12:
        failed_titles = await get_failed_scrapes("", today)
        if failed_titles:
            report = "⚠️ *Daily Scrape Failure Report*\n\nThe following newspapers could not be found today after 7 attempts:\n"
            for t in failed_titles:
                report += f"• {t}\n"
            try:
                await bot.send_message(chat_id=channel_id, text=report, parse_mode="Markdown")
                print("Failure report sent to channel.")
            except Exception as e:
                print(f"Failed to send report: {e}")

if __name__ == "__main__":
    asyncio.run(main())
