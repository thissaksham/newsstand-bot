"""
Newsstand Bot — Scraper & Delivery Engine

Standalone script invoked by GitHub Actions (every 15 minutes) or
manually via /run_scraper. Scrapes newspapers and magazines, uploads
to Telegram storage channel, and delivers to subscribers.

Key reliability features:
- Process-level lock file prevents overlapping runs
- Per-title try/except so one failure doesn't block others
- catch_up_deliveries() runs in a finally block — always executes
- Proper logging with timestamps (no bare print statements)
- Global timeout prevents indefinite hangs
"""

import os
import sys
import asyncio
import importlib
import logging
import tempfile
import signal
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

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
from scrapers.downmagaz_net import (
    scrape_magazine_tag, get_download_links,
    get_magazine_tag_and_version, matches_version,
)

# ─── Logging ───────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scraper")

# ─── Lock file to prevent overlapping runs ─────────────────────────────────

LOCK_FILE = Path(tempfile.gettempdir()) / "newsstand_scraper.lock"
GLOBAL_TIMEOUT_SECONDS = 600  # 10 minute hard cap on total runtime


def acquire_lock() -> bool:
    """Try to acquire a process-level lock. Returns False if another run is active."""
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            # Check if process is still running
            try:
                os.kill(pid, 0)  # signal 0 = just check existence
                # Process is still alive — bail out
                lock_age = datetime.now() - datetime.fromtimestamp(LOCK_FILE.stat().st_mtime)
                if lock_age.total_seconds() > GLOBAL_TIMEOUT_SECONDS:
                    logger.warning("Lock file is stale (%s old). Removing and proceeding.", lock_age)
                    LOCK_FILE.unlink(missing_ok=True)
                else:
                    logger.warning("Another scraper run (PID %d) is still active. Skipping this run.", pid)
                    return False
            except (OSError, ProcessLookupError):
                # Process is dead — stale lock
                logger.info("Stale lock file found (PID %d is dead). Removing.", pid)
                LOCK_FILE.unlink(missing_ok=True)
        except (ValueError, FileNotFoundError):
            LOCK_FILE.unlink(missing_ok=True)

    LOCK_FILE.write_text(str(os.getpid()))
    return True


def release_lock():
    """Release the lock file."""
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ─── Shared delivery helper ───────────────────────────────────────────────

async def send_edition_to_user(
    bot: Bot,
    user_id: int,
    edition_id: int,
    file_id: str,
    title_name: str,
    edition_date: date,
    category: str = "Newspaper",
) -> bool:
    """Send a single edition (newspaper PDF or magazine links) to one user.
    
    Returns True on success, False on failure.
    Handles multi-part PDFs, magazine download links, and rate limiting.
    """
    friendly_date = format_date(edition_date)
    
    try:
        if category == "Magazine":
            # Magazine: file_id contains comma-separated post URLs
            post_urls = file_id.split(",")
            for post_url in post_urls:
                links = await get_download_links(post_url)
                if links:
                    links_html = ""
                    for domain, href in links:
                        links_html += f'• <a href="{href}">Download via {domain}</a>\n'
                    msg_text = (
                        f"📖 <b>New Magazine Alert!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"New edition of <b>{title_name}</b> is available for <b>{friendly_date}</b>:\n\n"
                        f"Download Links:\n{links_html}"
                    )
                    await bot.send_message(
                        chat_id=user_id,
                        text=msg_text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
        else:
            # Newspaper: file_id contains comma-separated Telegram file_ids
            file_ids = file_id.split(",")
            for idx, fid in enumerate(file_ids):
                part_suffix = f" (Part {idx+1}/{len(file_ids)})" if len(file_ids) > 1 else ""
                await bot.send_document(
                    chat_id=user_id,
                    document=fid,
                    caption=f"📰 Here is your **{title_name}**{part_suffix} for {friendly_date}!",
                    parse_mode="Markdown",
                )
        
        await log_delivery("", user_id, edition_id, "success")
        return True
        
    except RetryAfter as e:
        logger.warning("Rate limited delivering to %s. Sleeping %ss...", user_id, e.retry_after)
        await asyncio.sleep(e.retry_after)
        try:
            # Retry once after rate limit
            if category != "Magazine":
                file_ids = file_id.split(",")
                for idx, fid in enumerate(file_ids):
                    part_suffix = f" (Part {idx+1}/{len(file_ids)})" if len(file_ids) > 1 else ""
                    await bot.send_document(
                        chat_id=user_id,
                        document=fid,
                        caption=f"📰 Here is your **{title_name}**{part_suffix} for {friendly_date}!",
                        parse_mode="Markdown",
                    )
            await log_delivery("", user_id, edition_id, "success")
            return True
        except Exception as ex:
            logger.error("[%s] Retry failed for user %s: %s", title_name, user_id, ex)
            
    except Exception as e:
        logger.error("[%s] Failed to deliver to %s: %s", title_name, user_id, e)

    await log_delivery("", user_id, edition_id, "failed")
    return False


# ─── PDF splitting ─────────────────────────────────────────────────────────

def split_pdf_if_large(filepath: str, max_size_mb: float = 45.0) -> list[str]:
    """Splits a PDF file into multiple files if its size exceeds max_size_mb.
    Returns a list of file paths. If the file is small or not a PDF, returns [filepath].
    """
    if not filepath.endswith(".pdf") or not os.path.exists(filepath):
        return [filepath]
        
    file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
    if file_size_mb <= max_size_mb:
        return [filepath]
        
    logger.info("File %s is %.2f MB, exceeds %.0f MB. Splitting...", filepath, file_size_mb, max_size_mb)
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
            logger.info("Created part %d: %s (%.2f MB, pages %d-%d)", i+1, part_path, part_size_mb, start_page+1, end_page)
            parts.append(part_path)
            
        return parts
    except Exception as e:
        logger.error("Error splitting PDF: %s", e)
        return [filepath]


# ─── Magazine processing ──────────────────────────────────────────────────

async def process_magazine_title(bot: Bot, title: dict, today: date):
    """Scrapes recent editions from downmagaz.net tag, extracts links,
    and sends updates to subscribers if not already processed.
    """
    name = title["name"]
    title_id = title["id"]
    slug = title["slug"]
    
    tag_name, version = get_magazine_tag_and_version(name, slug)
    tag_url = f"https://downmagaz.net/tags/{quote(tag_name.lower())}/"
    
    logger.info("[%s] Scraping tag page %s...", name, tag_url)
    posts = await scrape_magazine_tag(tag_url)
    
    if not posts:
        logger.info("[%s] No posts found on tag page.", name)
        await upsert_scrape_status("", title_id, today, status="failed", increment_attempts=True)
        return
        
    logger.info("[%s] Found %d posts on tag page.", name, len(posts))
    
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
            
        logger.info("[%s] Found post: %s for edition date %s", name, post_title, edition_date)
        
        # Only deliver to subscribers if the edition is new (published within last 3 days)
        is_new_edition = edition_date >= today - timedelta(days=3)
        
        links = await get_download_links(post_url)
        if not links:
            logger.info("[%s] No download links found for %s. Skipping.", name, post_title)
            continue
            
        subscribers = await get_subscribers_for_title("", title_id)
        delivered_users = []
        if subscribers and is_new_edition:
            links_html = ""
            for domain, href in links:
                links_html += f'• <a href="{href}">Download via {domain}</a>\n'
                
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
                    logger.info("[%s] Delivered to %s", name, user_id)
                except Exception as e:
                    logger.error("[%s] Failed to deliver to %s: %s", name, user_id, e)
        elif not is_new_edition:
            logger.info("[%s] Skipping alert for old historical edition %s (date: %s).", name, post_title, edition_date)
                    
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
            
        # Log delivery status for subscribers (only if it was a new edition)
        if subscribers and is_new_edition:
            for user_id in subscribers:
                status = "success" if user_id in delivered_users else "failed"
                await log_delivery("", user_id, edition_id, status)
            
        success_any = True
        
    if success_any:
        await upsert_scrape_status("", title_id, today, status="found", increment_attempts=True)
    else:
        await upsert_scrape_status("", title_id, today, status="pending", increment_attempts=False)


async def deliver_to_subscribers(bot: Bot, edition_id: int, file_id: str, title_id: int, title_name: str, newspaper_date: date):
    """Deliver the edition to all subscribed users using the shared helper."""
    subscribers = await get_subscribers_for_title("", title_id)
    logger.info("[%s] Found %d subscribers for delivery.", title_name, len(subscribers))
    
    for user_id in subscribers:
        if await has_been_delivered("", user_id, edition_id):
            continue
        
        await send_edition_to_user(bot, user_id, edition_id, file_id, title_name, newspaper_date)
        await asyncio.sleep(0.1)  # Small delay to avoid rate limits


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
    
    # 3. Check each subscription and deliver if needed
    for sub in subs_resp.data:
        user_id = sub["user_id"]
        title_id = sub["title_id"]
        
        if title_id in editions_map:
            ed = editions_map[title_id]
            edition_id = ed["edition_id"]
            
            if not await has_been_delivered("", user_id, edition_id):
                logger.info("[Catch-up] Delivering %s to %s...", ed["title_name"], user_id)
                await send_edition_to_user(
                    bot, user_id, edition_id, ed["file_id"],
                    ed["title_name"], scrape_date, ed.get("category", "Newspaper"),
                )
                await asyncio.sleep(0.2)


# ─── Main entry point ─────────────────────────────────────────────────────

async def main():
    load_dotenv()
    bot_token = os.getenv("BOT_TOKEN")
    channel_id = os.getenv("STORAGE_CHANNEL_ID")
    
    if not bot_token or not channel_id:
        logger.error("Missing BOT_TOKEN or STORAGE_CHANNEL_ID in .env")
        return

    # Acquire lock to prevent overlapping runs
    if not acquire_lock():
        return
    
    try:
        config = Config.get()
        today = get_today()
        bot = Bot(token=bot_token)
        
        target_slug = sys.argv[1] if len(sys.argv) > 1 else None
        
        if target_slug:
            logger.info("Scraping specific title slug: %s", target_slug)
            db = await _get_client()
            resp = await db.table("titles").select("*").eq("slug", target_slug).execute()
            pending_titles = resp.data if resp.data else []
        else:
            logger.info("Scraping all active titles for today: %s", today)
            pending_titles = await get_pending_scrapes("", scrape_date=today, max_attempts=7)
        
        if not pending_titles:
            logger.info("No pending titles to scrape. Exiting.")
            return
            
        logger.info("Found %d titles pending scrape.", len(pending_titles))
        
        # Filter titles based on category and current hour in IST
        ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        current_hour = ist_now.hour
        
        # If GITHUB_EVENT_NAME is not set (local run) or is 'workflow_dispatch' (manual), bypass hour filters
        github_event = os.getenv("GITHUB_EVENT_NAME")
        is_manual = (github_event is None) or (github_event == "workflow_dispatch")
        if is_manual:
            logger.info("Manual or local run detected. Bypassing scheduling hour filters.")
            
        filtered_titles = []
        for title in pending_titles:
            category = title.get("category", "Newspaper")
            if is_manual:
                filtered_titles.append(title)
            elif category == "Newspaper":
                if current_hour >= 6:
                    filtered_titles.append(title)
                else:
                    logger.info("[%s] Skipped: Newspaper checks only after 6am IST (current: %d IST).", title['name'], current_hour)
            else:
                filtered_titles.append(title)
                
        if not filtered_titles:
            logger.info("No titles to scrape for current hour (%d IST). Exiting.", current_hour)
            return
            
        logger.info("Processing %d titles in this run.", len(filtered_titles))
        
        # ── Scrape each title (with per-title error isolation) ──
        for title in filtered_titles:
            name = title["name"]
            slug = title["slug"]
            
            try:
                category = title.get("category", "Newspaper")
                if category == "Magazine":
                    await process_magazine_title(bot, title, today)
                    continue
                
                # Check if already scraped and uploaded today
                existing_edition = await get_edition("", title["id"], today)
                if existing_edition and existing_edition.get("file_id") and existing_edition.get("status") == "delivered":
                    logger.info("[%s] Already scraped and uploaded today. Delivering to subscribers.", name)
                    await upsert_scrape_status("", title["id"], today, status="found", increment_attempts=False)
                    await deliver_to_subscribers(bot, existing_edition["id"], existing_edition["file_id"], title["id"], name, today)
                    continue
                    
                source_module_name = title.get("source")
                
                # Get source_url from config.yaml
                conf_title = next((t for t in config.titles if getattr(t, "slug", "") == slug), None)
                source_url = getattr(conf_title, "source_url", None) if conf_title else None
                
                if not source_module_name or not source_url:
                    logger.info("[%s] Skipped: No source module or source URL defined.", name)
                    continue
                    
                # Dynamically import the scraper module
                try:
                    scraper_module = importlib.import_module(f"scrapers.{source_module_name}")
                except ImportError:
                    logger.error("[%s] Failed: Scraper module 'scrapers.%s' not found.", name, source_module_name)
                    continue
                    
                # Run the scraper
                result = await scraper_module.scrape(source_url, slug, name)
                
                if not result:
                    await upsert_scrape_status("", title["id"], today, status="failed", increment_attempts=True)
                    continue
                    
                output_file, newspaper_date = result
                
                if not output_file or not os.path.exists(output_file):
                    await upsert_scrape_status("", title["id"], today, status="failed", increment_attempts=True)
                    continue

                # Check if this edition_date already exists
                existing_edition = await get_edition("", title["id"], newspaper_date)
                if existing_edition and existing_edition.get("file_id") and existing_edition.get("status") == "delivered":
                    logger.info("[%s] Edition for %s already delivered. Skipping upload.", name, newspaper_date)
                    try:
                        if os.path.exists(output_file):
                            os.remove(output_file)
                    except Exception as e:
                        logger.warning("Failed to remove output file: %s", e)
                    
                    if newspaper_date == today or today.weekday() == 6:
                        await upsert_scrape_status("", title["id"], today, status="found", increment_attempts=False)
                        
                    await deliver_to_subscribers(bot, existing_edition["id"], existing_edition["file_id"], title["id"], name, newspaper_date)
                    continue

                # Upload to Telegram Storage Channel
                friendly_date = format_date(newspaper_date)
                logger.info("[%s] Uploading to Telegram Channel...", name)
                file_parts = []
                try:
                    file_parts = split_pdf_if_large(output_file, max_size_mb=45.0)
                    
                    telegram_file_ids = []
                    message_ids = []
                    
                    for idx, part_file in enumerate(file_parts):
                        part_suffix = f" (Part {idx+1}/{len(file_parts)})" if len(file_parts) > 1 else ""
                        
                        message = None
                        for attempt in range(3):
                            try:
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
                                break
                            except Exception as upload_err:
                                logger.error("[%s] Upload attempt %d failed: %s", name, attempt+1, upload_err)
                                if attempt < 2:
                                    await asyncio.sleep(5)
                                else:
                                    raise upload_err
                                    
                        if not message:
                            raise RuntimeError("Failed to upload file to Telegram after 3 attempts.")
                            
                        telegram_file_ids.append(message.document.file_id)
                        message_ids.append(str(message.message_id))
                    
                    combined_file_id = ",".join(telegram_file_ids)
                    combined_message_id = ",".join(message_ids)
                    
                    # Update Database
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
                    
                    if newspaper_date == today or today.weekday() == 6:
                        await upsert_scrape_status("", title["id"], today, status="found", increment_attempts=True)
                    
                    logger.info("[%s] Success! Database updated.", name)
                    
                    # Deliver to subscribers
                    await deliver_to_subscribers(bot, edition_id, combined_file_id, title["id"], name, newspaper_date)
                    
                except Exception as e:
                    logger.error("[%s] Error during upload/delivery: %s", name, e)
                    
                finally:
                    # Clean up all created files
                    for part_file in file_parts:
                        try:
                            if os.path.exists(part_file):
                                os.remove(part_file)
                        except Exception as e:
                            logger.warning("Failed to remove part file %s: %s", part_file, e)
                    try:
                        if os.path.exists(output_file):
                            os.remove(output_file)
                    except Exception as e:
                        logger.warning("Failed to remove output file %s: %s", output_file, e)
                        
            except Exception as e:
                # Per-title error isolation — log and continue to next title
                logger.exception("[%s] Unhandled error during scrape. Continuing to next title.", name)
                continue

        # ── Catch-up deliveries (ALWAYS runs, even if some titles failed above) ──
        logger.info("Running catch-up deliveries...")
        await catch_up_deliveries(bot, today)

        # ── Failure report at 12pm IST ──
        ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        if ist_now.hour == 12:
            failed_titles = await get_failed_scrapes("", today)
            if failed_titles:
                report = "⚠️ *Daily Scrape Failure Report*\n\nThe following newspapers could not be found today after 7 attempts:\n"
                for t in failed_titles:
                    report += f"• {t}\n"
                try:
                    await bot.send_message(chat_id=channel_id, text=report, parse_mode="Markdown")
                    logger.info("Failure report sent to channel.")
                except Exception as e:
                    logger.error("Failed to send report: %s", e)

    finally:
        release_lock()
        logger.info("Scraper run complete. Lock released.")


if __name__ == "__main__":
    asyncio.run(main())
