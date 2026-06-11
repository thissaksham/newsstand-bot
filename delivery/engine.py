"""
Delivery engine — scheduled scraping and fan-out to subscribers.

Uses APScheduler 3.x ``AsyncIOScheduler`` for cron-based scheduling and
relies on :mod:`scrapers.manager` for edition discovery and
:mod:`database.operations` for all persistence.
"""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import date, datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot
from telegram.error import RetryAfter, TelegramError

from config import Config, ScheduleConfig
from database import operations as db_ops
from scrapers.manager import ScraperManager

logger = logging.getLogger(__name__)

# Telegram rate-limit: max 30 messages per second to different chats
_SEND_INTERVAL: float = 1 / 30  # ~33 ms


class DeliveryEngine:
    """Orchestrates the daily scrape → store → deliver pipeline.

    Lifecycle::

        engine = DeliveryEngine(bot, scraper_manager, config)
        engine.start()      # schedules APScheduler jobs
        ...
        engine.stop()        # shuts down gracefully
    """

    def __init__(
        self,
        bot: Bot,
        scraper_manager: ScraperManager,
        config: Config,
    ) -> None:
        self._bot = bot
        self._scraper = scraper_manager
        self._cfg = config
        self._sched_cfg: ScheduleConfig = config.schedule
        self._tz = ZoneInfo(self._sched_cfg.timezone)
        self._db = str(config.db_path)
        self._storage_channel_id: int = config.storage_channel_id
        self._scheduler: Optional[AsyncIOScheduler] = None

    # ── scheduler lifecycle ──────────────────────────────────────────

    def start(self) -> None:
        """Create and start the APScheduler with delivery jobs."""
        self._scheduler = AsyncIOScheduler(timezone=self._tz)

        # Parse first_check time (e.g. "05:30")
        h, m = (int(x) for x in self._sched_cfg.first_check.split(":"))

        # Primary job: runs once at first_check time every day
        self._scheduler.add_job(
            self.run_delivery_cycle,
            trigger="cron",
            hour=h,
            minute=m,
            id="delivery_cycle_primary",
            replace_existing=True,
            misfire_grace_time=600,
        )

        # Retry job: runs every retry_interval minutes
        self._scheduler.add_job(
            self._retry_pending,
            trigger="interval",
            minutes=self._sched_cfg.retry_interval_minutes,
            id="delivery_cycle_retry",
            replace_existing=True,
            misfire_grace_time=300,
        )

        self._scheduler.start()
        logger.info(
            "DeliveryEngine started — first check at %s %s, "
            "retries every %d min, stop at %s",
            self._sched_cfg.first_check,
            self._sched_cfg.timezone,
            self._sched_cfg.retry_interval_minutes,
            self._sched_cfg.stop_retrying,
        )

    def stop(self) -> None:
        """Shut down the scheduler gracefully."""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("DeliveryEngine stopped.")

    # ── main delivery cycle ──────────────────────────────────────────

    async def run_delivery_cycle(self) -> None:
        """Full daily cycle: initialise scrape rows → scrape → store → deliver."""
        today = self._today()
        logger.info("=== Delivery cycle START for %s ===", today)

        # 1. Ensure every active title has a scrape-status row for today
        all_titles = await db_ops.get_all_titles(self._db)
        for title in all_titles:
            existing = await db_ops.get_scrape_status(
                self._db, title["id"], today
            )
            if existing is None:
                await db_ops.set_scrape_status(
                    self._db, title["id"], today, "pending"
                )

        # 2. Process pending titles
        stats = await self._process_pending_titles(today)

        # 3. Admin summary
        await self._send_admin_summary(today, stats)
        logger.info("=== Delivery cycle END for %s ===", today)

    # ── retry job ────────────────────────────────────────────────────

    async def _retry_pending(self) -> None:
        """Re-attempt titles that are still pending, if within the retry window."""
        now = datetime.now(self._tz)
        stop_h, stop_m = (int(x) for x in self._sched_cfg.stop_retrying.split(":"))
        stop_time = time(stop_h, stop_m)

        if now.time() >= stop_time:
            logger.debug("Past stop_retrying (%s) — skipping retry.", stop_time)
            return

        today = now.date()
        pending = await db_ops.get_pending_titles(self._db, today)
        if not pending:
            logger.debug("No pending titles for %s — nothing to retry.", today)
            return

        # Filter out titles that exceeded max_retries
        to_retry = [
            p for p in pending
            if p["attempts"] < self._sched_cfg.max_retries
        ]
        if not to_retry:
            logger.info("All pending titles have hit max retries.")
            return

        logger.info("Retrying %d pending titles for %s", len(to_retry), today)
        await self._process_pending_titles(today)

    # ── internal processing ──────────────────────────────────────────

    async def _process_pending_titles(
        self, today: date
    ) -> dict[str, int]:
        """Scrape + store + deliver for every pending title.

        Returns a stats dict: ``{found, failed, delivered, delivery_errors}``.
        """
        stats = {"found": 0, "failed": 0, "delivered": 0, "delivery_errors": 0}
        pending = await db_ops.get_pending_titles(self._db, today)

        for row in pending:
            title_id: int = row["title_id"]
            slug: str = row["title_slug"]
            title_name: str = row["title_name"]

            if row["attempts"] >= self._sched_cfg.max_retries:
                await db_ops.set_scrape_status(self._db, title_id, today, "failed")
                stats["failed"] += 1
                continue

            await db_ops.increment_attempts(self._db, title_id, today)

            try:
                result = await self._scraper.get_edition(slug, today)
            except Exception:
                logger.exception("Scraper error for %s", slug)
                stats["failed"] += 1
                continue

            if result is None:
                logger.info("Edition not yet available: %s", slug)
                continue

            # ── store in storage channel ─────────────────────────────
            try:
                edition_id, file_id = await self._store_edition(
                    title_id, title_name, today, result.download_url
                )
            except Exception:
                logger.exception("Failed to store %s", slug)
                stats["failed"] += 1
                await db_ops.set_scrape_status(self._db, title_id, today, "failed")
                continue

            await db_ops.set_scrape_status(self._db, title_id, today, "found")
            stats["found"] += 1

            # ── deliver to subscribers ───────────────────────────────
            subscribers = await db_ops.get_subscribers(self._db, title_id)
            for user_id in subscribers:
                success = await self._deliver_single(
                    user_id, edition_id, file_id, title_name, today
                )
                if success:
                    stats["delivered"] += 1
                else:
                    stats["delivery_errors"] += 1

        return stats

    async def _store_edition(
        self,
        title_id: int,
        title_name: str,
        edition_date: date,
        download_url: str,
    ) -> tuple[int, str]:
        """Download PDF → send to storage channel → persist file_id.

        Returns ``(edition_id, file_id)``.
        """
        pdf_bytes = await self._scraper.download_to_memory(download_url)
        if pdf_bytes is None:
            raise RuntimeError(f"Download returned empty bytes: {download_url}")

        caption = f"📰 {title_name} — {edition_date.strftime('%d %b %Y')}"
        doc = io.BytesIO(pdf_bytes)
        doc.name = f"{title_name.replace(' ', '_')}_{edition_date.isoformat()}.pdf"

        message = await self._bot.send_document(
            chat_id=self._storage_channel_id,
            document=doc,
            caption=caption,
        )

        file_id: str = message.document.file_id  # type: ignore[union-attr]
        message_id: int = message.message_id

        edition_id = await db_ops.add_edition(
            self._db, title_id, edition_date, download_url, status="stored"
        )
        await db_ops.update_edition_file_id(
            self._db, edition_id, file_id, message_id, status="stored"
        )
        logger.info("Stored %s (%s) — file_id=%s", title_name, edition_date, file_id)
        return edition_id, file_id

    async def _deliver_single(
        self,
        user_id: int,
        edition_id: int,
        file_id: str,
        title_name: str,
        edition_date: date,
    ) -> bool:
        """Send one edition to one user. Handles Telegram rate-limits."""
        caption = f"📰 {title_name} — {edition_date.strftime('%d %b %Y')}"
        try:
            await self._bot.send_document(
                chat_id=user_id,
                document=file_id,
                caption=caption,
            )
            await db_ops.log_delivery(self._db, user_id, edition_id, "success")
            await asyncio.sleep(_SEND_INTERVAL)
            return True

        except RetryAfter as exc:
            logger.warning(
                "Rate-limited sending to %d — sleeping %ds",
                user_id,
                exc.retry_after,
            )
            await asyncio.sleep(exc.retry_after)
            # Retry once after sleeping
            try:
                await self._bot.send_document(
                    chat_id=user_id,
                    document=file_id,
                    caption=caption,
                )
                await db_ops.log_delivery(self._db, user_id, edition_id, "success")
                return True
            except TelegramError:
                logger.exception("Retry also failed for user %d", user_id)
                await db_ops.log_delivery(self._db, user_id, edition_id, "failed")
                return False

        except TelegramError:
            logger.exception("Failed to deliver to user %d", user_id)
            await db_ops.log_delivery(self._db, user_id, edition_id, "failed")
            return False

    # ── on-demand delivery ───────────────────────────────────────────

    async def deliver_to_user(
        self, user_id: int, edition_id: int
    ) -> bool:
        """Deliver a specific edition to a user on demand.

        Looks up the edition's ``file_id`` from the database and sends
        it.  Returns ``True`` on success.
        """
        async with __import__("aiosqlite").connect(self._db) as conn:
            conn.row_factory = __import__("aiosqlite").Row
            cursor = await conn.execute(
                """
                SELECT e.*, t.name AS title_name
                FROM editions e
                JOIN titles t ON t.id = e.title_id
                WHERE e.id = ?
                """,
                (edition_id,),
            )
            row = await cursor.fetchone()

        if row is None or row["file_id"] is None:
            logger.warning("Edition %d not found or not yet stored.", edition_id)
            return False

        return await self._deliver_single(
            user_id,
            edition_id,
            row["file_id"],
            row["title_name"],
            date.fromisoformat(row["date"]),
        )

    # ── admin summary ────────────────────────────────────────────────

    async def _send_admin_summary(
        self, today: date, stats: dict[str, int]
    ) -> None:
        """Send a summary message to all admins."""
        text = (
            f"📊 *Delivery Summary — {today.strftime('%d %b %Y')}*\n\n"
            f"✅ Editions found: {stats['found']}\n"
            f"❌ Editions failed: {stats['failed']}\n"
            f"📬 Deliveries sent: {stats['delivered']}\n"
            f"⚠️ Delivery errors: {stats['delivery_errors']}"
        )
        for admin_id in self._cfg.admin_ids:
            try:
                await self._bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    parse_mode="Markdown",
                )
            except TelegramError:
                logger.exception("Could not notify admin %d", admin_id)

    # ── helpers ──────────────────────────────────────────────────────

    def _today(self) -> date:
        return datetime.now(self._tz).date()
