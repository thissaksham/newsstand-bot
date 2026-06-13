"""
Centralised configuration loader.

Reads ``config.yaml`` for application settings and ``.env`` for secrets.
Provides typed dataclasses and a singleton ``Config`` instance.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

# ── project root is the directory that contains this file ────────────
_PROJECT_ROOT = Path(__file__).resolve().parent

# Load .env (if present) so os.environ picks up secrets
load_dotenv(_PROJECT_ROOT / ".env")


# ── dataclasses ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class TitleConfig:
    """A single newspaper / magazine title from the config catalogue."""

    name: str
    slug: str
    language: str
    category: str
    source_url: str = ""
    scrape_website: str = ""





@dataclass(frozen=True)
class ScheduleConfig:
    """Delivery-schedule tunables."""

    timezone: str = "Asia/Kolkata"
    first_check: str = "05:30"
    retry_interval_minutes: int = 60
    stop_retrying: str = "12:00"
    max_retries: int = 6


@dataclass(frozen=True)
class BotConfig:
    """Top-level bot metadata."""

    name: str = "Newsstand Bot"
    welcome_message: str = "Welcome!"


# ── main Config class (singleton) ───────────────────────────────────

class Config:
    """Application-wide configuration (singleton).

    Usage::

        cfg = Config.get()
        print(cfg.bot_token)
        for t in cfg.titles:
            print(t.name, t.slug)
    """

    _instance: Optional["Config"] = None

    def __init__(self, yaml_path: Path | str | None = None) -> None:
        yaml_path = Path(yaml_path) if yaml_path else _PROJECT_ROOT / "config.yaml"
        with open(yaml_path, "r", encoding="utf-8") as fh:
            raw: dict = yaml.safe_load(fh)

        # ── secrets from environment ────────────────────────────────
        self._bot_token: str = os.environ.get("BOT_TOKEN", "")
        self._admin_ids: list[int] = [
            int(aid.strip())
            for aid in os.environ.get("ADMIN_IDS", "").split(",")
            if aid.strip().isdigit()
        ]

        # storage_channel_id can come from env (higher priority) or yaml
        env_channel = os.environ.get("STORAGE_CHANNEL_ID", "")
        yaml_channel = str(raw.get("storage", {}).get("channel_id", ""))
        channel_str = env_channel or yaml_channel
        self._storage_channel_id: int = (
            int(channel_str) if channel_str.lstrip("-").isdigit() else 0
        )

        # ── paths ───────────────────────────────────────────────────
        self._db_path: Path = _PROJECT_ROOT / "data" / "newsstand.db"
        self._download_dir: Path = _PROJECT_ROOT / "data" / "downloads"

        # Ensure data directories exist
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._download_dir.mkdir(parents=True, exist_ok=True)

        # ── bot section ─────────────────────────────────────────────
        bot_raw: dict = raw.get("bot", {})
        self._bot = BotConfig(
            name=bot_raw.get("name", "Newsstand Bot"),
            welcome_message=bot_raw.get("welcome_message", "Welcome!"),
        )

        # ── schedule ────────────────────────────────────────────────
        sched_raw: dict = raw.get("schedule", {})
        self._schedule = ScheduleConfig(
            timezone=sched_raw.get("timezone", "Asia/Kolkata"),
            first_check=sched_raw.get("first_check", "05:30"),
            retry_interval_minutes=int(sched_raw.get("retry_interval_minutes", 60)),
            stop_retrying=sched_raw.get("stop_retrying", "12:00"),
            max_retries=int(sched_raw.get("max_retries", 6)),
        )

        # ── titles ──────────────────────────────────────────────────
        self._titles: list[TitleConfig] = [
            TitleConfig(
                    name=t.get("name", "Unknown"),
                    slug=t.get("slug", "unknown"),
                    language=t.get("language", "English"),
                    category=t.get("category", "Newspaper"),
                    source_url=t.get("source_url", ""),
                    scrape_website=t.get("scrape_website", "careerswave_in"),
            )
            for t in raw.get("titles", [])
        ]



    # ── singleton accessor ──────────────────────────────────────────

    @classmethod
    def get(cls, yaml_path: Path | str | None = None) -> "Config":
        """Return the singleton ``Config`` instance, creating it on first call."""
        if cls._instance is None:
            cls._instance = cls(yaml_path)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Discard the cached singleton (useful in tests)."""
        cls._instance = None

    # ── public properties ───────────────────────────────────────────

    @property
    def bot_token(self) -> str:
        return self._bot_token

    @property
    def admin_ids(self) -> list[int]:
        return list(self._admin_ids)

    @property
    def storage_channel_id(self) -> int:
        return self._storage_channel_id

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def download_dir(self) -> Path:
        return self._download_dir

    @property
    def bot(self) -> BotConfig:
        return self._bot

    @property
    def schedule(self) -> ScheduleConfig:
        return self._schedule

    @property
    def titles(self) -> list[TitleConfig]:
        return list(self._titles)



    # ── convenience look-ups ────────────────────────────────────────

    def get_title(self, slug: str) -> TitleConfig | None:
        """Look up a title by slug. Returns ``None`` if not found."""
        for t in self._titles:
            if t.slug == slug:
                return t
        return None

    def get_titles_by_language(self, language: str) -> list[TitleConfig]:
        """Return all titles for a given language (case-insensitive)."""
        lang = language.lower()
        return [t for t in self._titles if t.language.lower() == lang]


