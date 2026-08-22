"""
Newsstand Bot — Scraper & Delivery Engine

Two ways to run:

1. Standalone (GitHub Actions / cron / admin shell)::

       python run_scrapers.py [slug]

   Creates its own Bot, takes a process-level file lock, runs one full cycle.

2. In-process (imported by the running bot on Render)::

       from run_scrapers import run_scrape_cycle
       await run_scrape_cycle(application.bot, only_categories={"Magazine"})

   Reuses the live Bot and an asyncio lock. This is what keeps magazine
   delivery timely — the always-on bot checks for new magazine editions every
   few minutes instead of waiting on GitHub Actions' (best-effort) cron.

Key reliability features:
- Process file lock (standalone) + asyncio lock (in-process) prevent overlap
- Per-title try/except so one failure doesn't block others
- catch_up_deliveries() always runs at the end of a cycle
- Recency checks stop historical back-issues from being pushed to subscribers
- All dynamic content is HTML-escaped before going to Telegram
"""

import os
import sys
import asyncio
import logging
import tempfile
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

from telegram import Bot
from telegram.error import RetryAfter, Forbidden
from dotenv import load_dotenv

from config import Config
from database.operations import (
    add_edition, update_edition_status, get_pending_scrapes,
    get_subscribers_for_title, log_delivery, has_been_delivered,
    upsert_scrape_status, get_failed_scrapes, _get_client,
    get_edition, prune_delivery_log,
)
from utils.helpers import (
    get_today, format_date_long, html_escape, is_recent_edition,
    magazine_date_label, download_url_to_bytes, is_url, pdf_buffer,
)
from scrapers import find_newspaper_link
from scrapers.downmagaz_net import (
    get_download_links,
    get_magazine_tag_and_version, matches_version, scrape_magazine_tag,
)

# ─── Logging ───────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scraper")

# ─── Overlap protection ────────────────────────────────────────────────────

LOCK_FILE = Path(tempfile.gettempdir()) / "newsstand_scraper.lock"
GLOBAL_TIMEOUT_SECONDS = 600  # 10 minute hard cap on total runtime

# In-process guard so the scheduler job and an admin-triggered run can't run a
# full cycle on top of each other. Targeted single-title runs are not gated.
_CYCLE_LOCK = asyncio.Lock()

# Dates of once-a-day housekeeping (per process), so the every-15-min cycles
# don't repeat them: the failure report and the delivery_log prune.
_last_failure_report_date: date | None = None
_last_prune_date: date | None = None


def acquire_lock() -> bool:
    """Try to acquire a process-level file lock. Returns False if another run is active."""
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            try:
                os.kill(pid, 0)  # signal 0 = just check existence
                lock_age = datetime.now() - datetime.fromtimestamp(LOCK_FILE.stat().st_mtime)
                if lock_age.total_seconds() > GLOBAL_TIMEOUT_SECONDS:
                    logger.warning("Lock file is stale (%s old). Removing and proceeding.", lock_age)
                    LOCK_FILE.unlink(missing_ok=True)
                else:
                    logger.warning("Another scraper run (PID %d) is still active. Skipping this run.", pid)
                    return False
            except OSError:
                # Process is gone (or os.kill unsupported on this platform) — stale lock.
                logger.info("Stale lock file found (PID %d not alive). Removing.", pid)
                LOCK_FILE.unlink(missing_ok=True)
        except (ValueError, FileNotFoundError):
            LOCK_FILE.unlink(missing_ok=True)

    LOCK_FILE.write_text(str(os.getpid()))
    return True


def release_lock():
    """Release the file lock."""
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
    """Send a single edition's download link(s) to one user.

    Both newspapers and magazines are link-shares: newspapers carry a direct
    source link (e.g. Google Drive) in ``file_id``; magazines carry downmagaz
    post URLs that are re-scraped into mirror links at send time. Returns True on
    success, False on failure, with a single rate-limit retry.
    """
    safe_name = html_escape(title_name)

    async def _do_send() -> None:
        if category == "Magazine":
            # Monthlies collapse to "Jun 2026"; dated issues keep the full date.
            friendly_date = magazine_date_label(title_name, edition_date)
            # file_id holds comma-separated downmagaz post URLs.
            sent_any = False
            for post_url in file_id.split(","):
                links = await get_download_links(post_url)
                if not links:
                    continue
                links_html = "".join(
                    f'• <a href="{html_escape(href)}">Download via {html_escape(domain)}</a>\n'
                    for domain, href in links
                )
                msg_text = (
                    f"📖 <b>New Magazine Alert!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"New edition of <b>{safe_name}</b> is available for <b>{friendly_date}</b>:\n\n"
                    f"Download Links:\n{links_html}"
                )
                await bot.send_message(
                    chat_id=user_id, text=msg_text,
                    parse_mode="HTML", disable_web_page_preview=True,
                )
                sent_any = True
                # One message per edition — if file_id holds several post URLs
                # (e.g. a version-less subscription matching regional variants),
                # don't fire a message for each and flood the subscriber.
                break
            if not sent_any:
                # Post has no download links yet — fail so it's retried next
                # cycle instead of being silently marked delivered.
                raise RuntimeError(f"No download links available yet for {title_name}")
        elif category == "The Hindu/Indian Express":
            # Premium titles are sent as Telegram documents. ``file_id`` should be
            # a Telegram document file_id; if it is still a URL the link has
            # expired and we cannot re-download it during catch-up.
            stored = file_id.split(",")[0]
            if is_url(stored):
                raise RuntimeError("Expired premium URL in catch-up")
            ok = await _send_premium_pdf_file_id(bot, user_id, title_name, edition_date, stored)
            if not ok:
                raise RuntimeError("Failed to send premium PDF")
            return

        else:
            # file_id holds the newspaper's direct source download link.
            link = file_id.split(",")[0]
            if not link.startswith(("http://", "https://")):
                # Legacy edition stored as a Telegram file_id — not a link. Don't
                # render a broken link; just skip (it ages out of the window).
                logger.warning("[%s] Skipping edition with non-link file_id (legacy data).", title_name)
                return

            link_l = link.lower()
            if "indiags.com" in link_l:
                source_label = "indiags.com"
            elif "drive.google.com" in link_l or "google.com" in link_l:
                source_label = "Google Drive"
            else:
                source_label = "source"

            msg_text = (
                f"📰 <b>{safe_name}</b> — {format_date_long(edition_date)}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Your edition is ready:\n"
                f'<a href="{html_escape(link)}">⬇️ Download ({html_escape(source_label)})</a>'
            )
            await bot.send_message(
                chat_id=user_id, text=msg_text,
                parse_mode="HTML", disable_web_page_preview=True,
            )

    try:
        await _do_send()
        await log_delivery("", user_id, edition_id, "success")
        return True
    except Forbidden as e:
        # User blocked the bot / deactivated / never started a chat — unreachable.
        # Record it as done so catch-up doesn't retry them every cycle forever
        # (which otherwise bloats delivery_log and wastes API calls).
        logger.warning("[%s] User %s is unreachable (%s) — not retrying.", title_name, user_id, e)
        await log_delivery("", user_id, edition_id, "success")
        return True
    except RetryAfter as e:
        logger.warning("Rate limited delivering to %s. Sleeping %ss...", user_id, e.retry_after)
        await asyncio.sleep(e.retry_after)
        try:
            await _do_send()
            await log_delivery("", user_id, edition_id, "success")
            return True
        except Forbidden as ex:
            logger.warning("[%s] User %s unreachable on retry (%s) — not retrying.", title_name, user_id, ex)
            await log_delivery("", user_id, edition_id, "success")
            return True
        except Exception as ex:
            logger.error("[%s] Retry failed for user %s: %s", title_name, user_id, ex)
    except Exception as e:
        logger.error("[%s] Failed to deliver to %s: %s", title_name, user_id, e)

    await log_delivery("", user_id, edition_id, "failed")
    return False


# ─── Premium PDF delivery (The Hindu / Indian Express) ─────────────────────

async def _send_premium_pdf_bytes(
    bot: Bot, user_id: int, title_name: str, edition_date: date, pdf_bytes: bytes
) -> str | None:
    """Send PDF bytes to one user and return the Telegram document ``file_id``.

    Returns ``None`` on failure. ``Forbidden`` users are treated as delivered so
    catch-up does not retry them forever.
    """
    safe_name = html_escape(title_name)
    caption = (
        f"📰 <b>{safe_name}</b> — {format_date_long(edition_date)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Your edition is ready. 📄"
    )
    filename = f"{title_name.replace(' ', '_')}_{edition_date.isoformat()}.pdf"

    async def _send():
        return await bot.send_document(
            chat_id=user_id,
            document=pdf_buffer(pdf_bytes),
            caption=caption,
            parse_mode="HTML",
            filename=filename,
        )

    try:
        msg = await _send()
        return msg.document.file_id if msg.document else None
    except Forbidden as e:
        logger.warning("[%s] User %s unreachable (%s) — not retrying.", title_name, user_id, e)
        return None
    except RetryAfter as e:
        logger.warning("Rate limited delivering PDF to %s. Sleeping %ss...", user_id, e.retry_after)
        await asyncio.sleep(e.retry_after)
        try:
            msg = await _send()
            return msg.document.file_id if msg.document else None
        except Forbidden as ex:
            logger.warning("[%s] User %s unreachable on retry (%s) — not retrying.", title_name, user_id, ex)
            return None
        except Exception as ex:
            logger.error("[%s] Retry failed for user %s: %s", title_name, user_id, ex)
            return None
    except Exception as e:
        logger.error("[%s] Failed to send PDF to %s: %s", title_name, user_id, e)
        return None


async def _send_premium_pdf_file_id(
    bot: Bot, user_id: int, title_name: str, edition_date: date, file_id: str
) -> bool:
    """Send an already-uploaded PDF to one user using its Telegram ``file_id``."""
    safe_name = html_escape(title_name)
    caption = (
        f"📰 <b>{safe_name}</b> — {format_date_long(edition_date)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Your edition is ready. 📄"
    )

    async def _send():
        await bot.send_document(
            chat_id=user_id,
            document=file_id,
            caption=caption,
            parse_mode="HTML",
        )

    try:
        await _send()
        return True
    except Forbidden as e:
        logger.warning("[%s] User %s unreachable (%s) — not retrying.", title_name, user_id, e)
        return True
    except RetryAfter as e:
        logger.warning("Rate limited delivering PDF to %s. Sleeping %ss...", user_id, e.retry_after)
        await asyncio.sleep(e.retry_after)
        try:
            await _send()
            return True
        except Forbidden as ex:
            logger.warning("[%s] User %s unreachable on retry (%s) — not retrying.", title_name, user_id, ex)
            return True
        except Exception as ex:
            logger.error("[%s] Retry failed for user %s: %s", title_name, user_id, ex)
            return False
    except Exception as e:
        logger.error("[%s] Failed to send PDF to %s: %s", title_name, user_id, e)
        return False


async def deliver_premium_pdf_to_subscribers(
    bot: Bot, edition_id: int, go_url: str, title_id: int, title_name: str, edition_date: date
) -> bool:
    """Download a premium PDF once and send it to all subscribers.

    The first reachable subscriber receives the bytes; the returned Telegram
    ``file_id`` is stored and reused for everyone else (and for catch-up later).
    """
    logger.info("[%s] Downloading premium PDF...", title_name)
    pdf_bytes = await download_url_to_bytes(go_url)
    if not pdf_bytes:
        logger.error("[%s] Failed to download premium PDF from %s", title_name, go_url)
        return False

    size_mb = len(pdf_bytes) / (1024 * 1024)
    if size_mb > 20:
        logger.error("[%s] Premium PDF is %.1f MB, exceeds Telegram 20 MB limit.", title_name, size_mb)
        return False

    logger.info("[%s] Premium PDF downloaded: %.1f MB", title_name, size_mb)

    subscribers = await get_subscribers_for_title("", title_id)
    if not subscribers:
        logger.info("[%s] No subscribers to deliver PDF to.", title_name)
        return False

    file_id: str | None = None
    sent_any = False

    for user_id in subscribers:
        if await has_been_delivered("", user_id, edition_id):
            continue

        if file_id is None:
            # Keep trying subscribers until one is reachable and returns a file_id.
            # _send_premium_pdf_bytes logs Forbidden internally and returns None.
            file_id = await _send_premium_pdf_bytes(bot, user_id, title_name, edition_date, pdf_bytes)
            if file_id:
                await log_delivery("", user_id, edition_id, "success")
                sent_any = True
        else:
            ok = await _send_premium_pdf_file_id(bot, user_id, title_name, edition_date, file_id)
            if ok:
                await log_delivery("", user_id, edition_id, "success")
                sent_any = True

        await asyncio.sleep(0.2)

    if file_id:
        await update_edition_status("", edition_id=edition_id, status="delivered", file_id=file_id)
        logger.info("[%s] Premium PDF delivered. Stored Telegram file_id.", title_name)
    else:
        logger.error("[%s] Could not obtain a Telegram file_id; no PDF was delivered.", title_name)

    return sent_any


# ─── Magazine processing ──────────────────────────────────────────────────

async def process_magazine_title(bot: Bot, title: dict, today: date):
    """Scrape recent editions from a downmagaz.net tag, extract links, and send
    updates to subscribers if not already processed.
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
        # Keep magazines pollable (don't let attempts cap them out): a tag page
        # can be empty transiently, so just retry next cycle.
        await upsert_scrape_status("", title_id, today, status="pending", increment_attempts=False)
        return

    logger.info("[%s] Found %d posts on tag page.", name, len(posts))

    for post in posts:
        post_title = post["title"]
        post_url = post["url"]
        edition_date = post["date"]

        if not matches_version(post_title, version):
            continue

        edition = await get_edition("", title_id, edition_date)

        processed_urls = []
        if edition and edition.get("file_id"):
            processed_urls = edition["file_id"].split(",")

        if post_url in processed_urls:
            continue

        logger.info("[%s] Recording edition: %s (%s)", name, post_title, edition_date)

        # Only RECORD the post URL against the edition here — delivery is done by
        # catch_up_deliveries(), which sends the single LATEST recent edition to
        # each subscriber. Per-post delivery here meant a daily magazine (every
        # post matches when version is None, and every same-month post counts as
        # "new") tried to send dozens of messages per run, tripping Telegram rate
        # limits so the real new edition never got through.
        if not edition:
            new_edition_id = await add_edition(
                db_path="", title_id=title_id, edition_date=edition_date,
                download_url=post_url, status="pending",
            )
            await update_edition_status(db_path="", edition_id=new_edition_id, status="delivered", file_id=post_url)
        else:
            new_file_id = f"{edition.get('file_id') or ''},{post_url}".strip(",")
            await update_edition_status(db_path="", edition_id=edition["id"], status="delivered", file_id=new_file_id)

    # Magazines are never marked "found": unlike daily newspapers, a new issue
    # can drop at any hour, so we keep polling every cycle (every ~15 min).
    # Re-delivery is prevented by the processed-post-URL dedup above, not by the
    # scrape status — marking "found" here is what made same-day new editions
    # get skipped until the next day.
    await upsert_scrape_status("", title_id, today, status="pending", increment_attempts=False)


async def deliver_to_subscribers(
    bot: Bot, edition_id: int, file_id: str, title_id: int, title_name: str,
    newspaper_date: date, category: str = "Newspaper",
):
    """Deliver an edition to all subscribed users using the shared helper."""
    subscribers = await get_subscribers_for_title("", title_id)
    logger.info("[%s] Found %d subscribers for delivery.", title_name, len(subscribers))

    for user_id in subscribers:
        if await has_been_delivered("", user_id, edition_id):
            continue
        await send_edition_to_user(bot, user_id, edition_id, file_id, title_name, newspaper_date, category)
        await asyncio.sleep(0.1)  # Small delay to avoid rate limits


async def catch_up_deliveries(bot: Bot, scrape_date: date):
    """Deliver recent editions to any subscribers who haven't received them yet."""
    db = await _get_client()

    subs_resp = await db.table("subscriptions").select("user_id, title_id").execute()
    if not subs_resp.data:
        return

    # Look back far enough to find month-dated magazine editions; recency is then
    # enforced per-row below so we never push genuinely old back-issues.
    window_start = (scrape_date - timedelta(days=31)).isoformat()
    editions_resp = await db.table("editions").select(
        "id, title_id, date, file_id, titles(name, category)"
    ).gte("date", window_start).lte("date", scrape_date.isoformat()).execute()
    if not editions_resp.data:
        return

    editions_map = {}
    for row in editions_resp.data:
        if row.get("file_id"):
            tid = row["title_id"]
            if tid not in editions_map or row["date"] > editions_map[tid]["date"]:
                editions_map[tid] = {
                    "edition_id": row["id"],
                    "file_id": row["file_id"],
                    "date": row["date"],
                    "title_name": row["titles"]["name"] if row.get("titles") else f"Title #{tid}",
                    "category": row["titles"]["category"] if row.get("titles") else "Newspaper",
                }

    for sub in subs_resp.data:
        user_id = sub["user_id"]
        title_id = sub["title_id"]

        if title_id not in editions_map:
            continue

        ed = editions_map[title_id]
        edition_id = ed["edition_id"]
        category = ed.get("category", "Newspaper")
        edition_date = date.fromisoformat(ed["date"]) if isinstance(ed["date"], str) else ed["date"]

        # Only catch up genuinely recent editions — never resurrect back-issues.
        if not is_recent_edition(edition_date, scrape_date, category):
            continue

        if not await has_been_delivered("", user_id, edition_id):
            logger.info("[Catch-up] Delivering %s to %s...", ed["title_name"], user_id)
            await send_edition_to_user(
                bot, user_id, edition_id, ed["file_id"], ed["title_name"], edition_date, category,
            )
            await asyncio.sleep(0.2)


# ─── Scrape cycle (reusable core) ──────────────────────────────────────────

async def _run_scrape_cycle_inner(bot: Bot, target_slug, only_categories, is_manual):
    config = Config.get()
    today = get_today()

    if target_slug:
        logger.info("Scraping specific title slug: %s", target_slug)
        db = await _get_client()
        resp = await db.table("titles").select("*").eq("slug", target_slug).execute()
        pending_titles = resp.data if resp.data else []
    else:
        logger.info("Scraping active titles for today: %s", today)
        pending_titles = await get_pending_scrapes("", scrape_date=today, max_attempts=7)

    if only_categories:
        pending_titles = [t for t in pending_titles if t.get("category", "Newspaper") in only_categories]

    if not pending_titles:
        logger.info("No pending titles to scrape. Exiting cycle.")
        # Still run catch-up so freshly-stored editions reach subscribers.
        await catch_up_deliveries(bot, today)
        return

    logger.info("Found %d titles pending scrape.", len(pending_titles))

    # Newspapers are only checked after 6am IST (papers aren't out earlier);
    # manual/on-demand runs bypass the window.
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    current_hour = ist_now.hour
    if is_manual:
        logger.info("Manual/on-demand run — bypassing scheduling hour filters.")

    filtered_titles = []
    for title in pending_titles:
        category = title.get("category", "Newspaper")
        if is_manual or category != "Newspaper":
            filtered_titles.append(title)
        elif current_hour >= 6:
            filtered_titles.append(title)
        else:
            logger.info("[%s] Skipped: Newspaper checks only after 6am IST (current: %d IST).", title["name"], current_hour)

    if not filtered_titles:
        logger.info("No titles to scrape for current hour (%d IST).", current_hour)
        await catch_up_deliveries(bot, today)
        return

    logger.info("Processing %d titles in this cycle.", len(filtered_titles))

    for title in filtered_titles:
        name = title["name"]
        slug = title["slug"]

        try:
            category = title.get("category", "Newspaper")
            if category == "Magazine":
                await process_magazine_title(bot, title, today)
                continue

            existing_edition = await get_edition("", title["id"], today)
            if existing_edition and existing_edition.get("file_id") and existing_edition.get("status") == "delivered":
                logger.info("[%s] Already found today. Delivering link to subscribers.", name)
                await upsert_scrape_status("", title["id"], today, status="found", increment_attempts=False)
                await deliver_to_subscribers(
                    bot, existing_edition["id"], existing_edition["file_id"],
                    title["id"], name, today, category,
                )
                continue

            source_module_name = title.get("source")
            conf_title = next((t for t in config.titles if getattr(t, "slug", "") == slug), None)
            source_url = getattr(conf_title, "source_url", None) if conf_title else None

            if not source_module_name or not source_url:
                logger.info("[%s] Skipped: No source module or source URL defined.", name)
                continue

            # Find the latest available edition's link (today, then up to 3 days
            # back), with dailyepaper.in as automatic fallback if the primary lags.
            result = await find_newspaper_link(
                name, source_module_name, source_url,
                [today - timedelta(days=n) for n in range(4)],
            )
            if not result:
                await upsert_scrape_status("", title["id"], today, status="failed", increment_attempts=True)
                continue

            newspaper_date, download_link = result

            existing_edition = await get_edition("", title["id"], newspaper_date)
            if existing_edition and existing_edition.get("file_id") and existing_edition.get("status") == "delivered":
                logger.info("[%s] Edition for %s already recorded. Delivering link.", name, newspaper_date)
                if newspaper_date == today or today.weekday() == 6:
                    await upsert_scrape_status("", title["id"], today, status="found", increment_attempts=False)
                await deliver_to_subscribers(
                    bot, existing_edition["id"], existing_edition["file_id"],
                    title["id"], name, newspaper_date, category,
                )
                continue

            # Premium titles: download the actual PDF and send as Telegram documents
            # because the source /go/ links expire within a minute.
            if category == "The Hindu/Indian Express":
                if existing_edition:
                    edition_id = existing_edition["id"]
                else:
                    edition_id = await add_edition(
                        db_path="", title_id=title["id"], edition_date=newspaper_date,
                        download_url=download_link, status="stored",
                    )

                ok = await deliver_premium_pdf_to_subscribers(
                    bot, edition_id, download_link, title["id"], name, newspaper_date,
                )
                if not ok:
                    await upsert_scrape_status("", title["id"], today, status="failed", increment_attempts=True)
                    continue

                if newspaper_date == today or today.weekday() == 6:
                    await upsert_scrape_status("", title["id"], today, status="found", increment_attempts=True)
                continue

            # Regular link-share titles (Google Drive, etc.)
            if existing_edition:
                edition_id = existing_edition["id"]
            else:
                edition_id = await add_edition(
                    db_path="", title_id=title["id"], edition_date=newspaper_date,
                    download_url=download_link, status="stored",
                )
            await update_edition_status("", edition_id=edition_id, status="delivered", file_id=download_link)

            if newspaper_date == today or today.weekday() == 6:
                await upsert_scrape_status("", title["id"], today, status="found", increment_attempts=True)

            logger.info("[%s] Link recorded for %s. Delivering to subscribers.", name, newspaper_date)
            await deliver_to_subscribers(bot, edition_id, download_link, title["id"], name, newspaper_date, category)

        except Exception:
            logger.exception("[%s] Unhandled error during scrape. Continuing to next title.", name)
            continue

    logger.info("Running catch-up deliveries...")
    await catch_up_deliveries(bot, today)

    # Once-a-day housekeeping (per process): prune delivery_log so it doesn't grow
    # unbounded over the months. Pruned rows are far outside the catch-up window,
    # so this never triggers a re-delivery.
    if target_slug is None:
        global _last_prune_date
        if _last_prune_date != today:
            _last_prune_date = today
            try:
                removed = await prune_delivery_log("", days=90)
                if removed:
                    logger.info("Pruned %d delivery_log rows older than 90 days.", removed)
            except Exception as e:
                logger.warning("delivery_log prune failed: %s", e)

    # Daily failure report at 12pm IST — DM'd to the bot admins, at most once per
    # day per process (cycles run every ~15 min, so without the date guard the
    # noon hour would fire it repeatedly).
    if target_slug is None and not only_categories:
        global _last_failure_report_date
        ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        admin_ids = config.admin_ids
        if ist_now.hour == 12 and admin_ids and _last_failure_report_date != today:
            _last_failure_report_date = today
            failed_titles = await get_failed_scrapes("", today)
            if failed_titles:
                report = (
                    "⚠️ <b>Daily Scrape Failure Report</b>\n\n"
                    "These newspapers could not be found today after 7 attempts:\n"
                )
                for t in failed_titles:
                    report += f"• {html_escape(t)}\n"
                for admin_id in admin_ids:
                    try:
                        await bot.send_message(chat_id=admin_id, text=report, parse_mode="HTML")
                    except Exception as e:
                        logger.error("Failed to send failure report to admin %s: %s", admin_id, e)
                logger.info("Failure report sent to %d admin(s).", len(admin_ids))


async def run_scrape_cycle(bot: Bot, target_slug: str | None = None, only_categories: set | None = None, is_manual: bool | None = None):
    """Run one scrape + delivery cycle using an already-built Bot.

    Parameters
    ----------
    bot:
        A live ``telegram.Bot`` (the running app's bot, in-process).
    target_slug:
        If given, scrape only that title (on-demand) and skip the cycle lock.
    only_categories:
        Restrict the cycle to these categories, e.g. ``{"Magazine"}``.
    is_manual:
        Bypass the newspaper hour window. Defaults to True for targeted runs,
        otherwise inferred from ``GITHUB_EVENT_NAME``.
    """
    if is_manual is None:
        if target_slug:
            is_manual = True
        else:
            github_event = os.getenv("GITHUB_EVENT_NAME")
            is_manual = (github_event is None) or (github_event == "workflow_dispatch")

    # Targeted on-demand runs WAIT for the lock so they serialize with the
    # scheduled cycle — this avoids two cycles processing (and double-alerting)
    # the same brand-new magazine edition at once.
    if target_slug is not None:
        async with _CYCLE_LOCK:
            await _run_scrape_cycle_inner(bot, target_slug, only_categories, is_manual)
        return

    # A repeating full/category cycle skips rather than piling up if one is live.
    if _CYCLE_LOCK.locked():
        logger.info("A scrape cycle is already running in-process. Skipping this trigger.")
        return
    async with _CYCLE_LOCK:
        await _run_scrape_cycle_inner(bot, None, only_categories, is_manual)


# ─── Standalone entry point ────────────────────────────────────────────────

async def main():
    load_dotenv()
    bot_token = os.getenv("BOT_TOKEN")

    if not bot_token:
        logger.error("Missing BOT_TOKEN in environment.")
        return

    if not acquire_lock():
        return

    try:
        target_slug = sys.argv[1] if len(sys.argv) > 1 else None
        # `async with` initialises and cleanly shuts down the Bot's HTTP backend
        # (required for a standalone Bot in PTB v21).
        async with Bot(token=bot_token) as bot:
            await run_scrape_cycle(bot, target_slug=target_slug)
    finally:
        release_lock()
        logger.info("Scraper run complete. Lock released.")


if __name__ == "__main__":
    asyncio.run(main())
