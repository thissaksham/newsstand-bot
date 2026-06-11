"""
SQLite schema definitions and database initialisation.

Call :func:`init_db` once at startup to ensure every table exists.
All tables use ``IF NOT EXISTS`` so the function is safe to call repeatedly.
"""

from __future__ import annotations

import asyncpg

# ── SQL statements ───────────────────────────────────────────────────


_USERS_SQL = """\
CREATE TABLE IF NOT EXISTS users (
    user_id     BIGINT PRIMARY KEY,           -- Telegram user id
    username    TEXT,
    first_name  TEXT,
    is_admin    INTEGER NOT NULL DEFAULT 0,   -- 0 / 1
    joined_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_TITLES_SQL = """\
CREATE TABLE IF NOT EXISTS titles (
    id          SERIAL PRIMARY KEY,
    name        TEXT    NOT NULL,
    slug        TEXT    NOT NULL UNIQUE,
    language    TEXT    NOT NULL,
    category    TEXT    NOT NULL DEFAULT 'Newspaper',
    source      TEXT,                           -- scraper source name
    is_active   INTEGER NOT NULL DEFAULT 1,     -- 0 / 1
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_EDITIONS_SQL = """\
CREATE TABLE IF NOT EXISTS editions (
    id            SERIAL PRIMARY KEY,
    title_id      INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    date          TEXT    NOT NULL,              -- ISO-8601 date
    file_id       TEXT,                          -- Telegram cached file_id
    message_id    INTEGER,                       -- storage-channel message id
    download_url  TEXT,
    status        TEXT    NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'stored', 'delivered', 'failed')),
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (title_id, date)
);
"""

_SUBSCRIPTIONS_SQL = """\
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id       BIGINT NOT NULL REFERENCES users(user_id)  ON DELETE CASCADE,
    title_id      INTEGER NOT NULL REFERENCES titles(id)      ON DELETE CASCADE,
    subscribed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, title_id)
);
"""

_PACKS_SQL = """\
CREATE TABLE IF NOT EXISTS packs (
    id          SERIAL PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_PACK_TITLES_SQL = """\
CREATE TABLE IF NOT EXISTS pack_titles (
    pack_id   INTEGER NOT NULL REFERENCES packs(id)   ON DELETE CASCADE,
    title_id  INTEGER NOT NULL REFERENCES titles(id)  ON DELETE CASCADE,
    PRIMARY KEY (pack_id, title_id)
);
"""

_DELIVERY_LOG_SQL = """\
CREATE TABLE IF NOT EXISTS delivery_log (
    id           SERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users(user_id)    ON DELETE CASCADE,
    edition_id   INTEGER NOT NULL REFERENCES editions(id)      ON DELETE CASCADE,
    delivered_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status       TEXT    NOT NULL DEFAULT 'success'
                 CHECK (status IN ('success', 'failed'))
);
"""

_DAILY_SCRAPE_STATUS_SQL = """\
CREATE TABLE IF NOT EXISTS daily_scrape_status (
    id              SERIAL PRIMARY KEY,
    title_id        INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    date            TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'found', 'failed')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMP,
    UNIQUE (title_id, date)
);
"""

# Useful indices
_INDICES_SQL = """\
CREATE INDEX IF NOT EXISTS idx_editions_title_date   ON editions(title_id, date);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user    ON subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_title   ON subscriptions(title_id);
CREATE INDEX IF NOT EXISTS idx_delivery_log_user     ON delivery_log(user_id);
CREATE INDEX IF NOT EXISTS idx_delivery_log_edition  ON delivery_log(edition_id);
CREATE INDEX IF NOT EXISTS idx_scrape_status_date    ON daily_scrape_status(date);
"""

# Collect all DDL in order
_ALL_DDL: list[str] = [
    _USERS_SQL,
    _TITLES_SQL,
    _EDITIONS_SQL,
    _SUBSCRIPTIONS_SQL,
    _PACKS_SQL,
    _PACK_TITLES_SQL,
    _DELIVERY_LOG_SQL,
    _DAILY_SCRAPE_STATUS_SQL,
    _INDICES_SQL,
]


# ── public API ───────────────────────────────────────────────────────

async def init_db(db_url: str) -> None:
    """Create all tables and indices if they do not already exist."""
    conn = await asyncpg.connect(db_url)
    try:
        for ddl in _ALL_DDL:
            await conn.execute(ddl)
    finally:
        await conn.close()
