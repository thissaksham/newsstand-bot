-- Newsstand Bot Schema for Supabase

CREATE TABLE IF NOT EXISTS users (
    user_id     BIGINT PRIMARY KEY,           -- Telegram user id
    username    TEXT,
    first_name  TEXT,
    is_admin    INTEGER NOT NULL DEFAULT 0,   -- 0 / 1
    joined_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS titles (
    id          SERIAL PRIMARY KEY,
    name        TEXT    NOT NULL,
    slug        TEXT    NOT NULL UNIQUE,
    language    TEXT    NOT NULL,
    category    TEXT    NOT NULL DEFAULT 'Newspaper',
    source      TEXT,                           -- scraper source name
    is_active   INTEGER NOT NULL DEFAULT 1,     -- 0 / 1
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS editions (
    id            SERIAL PRIMARY KEY,
    title_id      INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    date          DATE    NOT NULL,              -- ISO-8601 date
    file_id       TEXT,                          -- Telegram cached file_id
    message_id    INTEGER,                       -- storage-channel message id
    download_url  TEXT,
    status        TEXT    NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'stored', 'delivered', 'failed')),
    created_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    UNIQUE (title_id, date)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    user_id       BIGINT NOT NULL REFERENCES users(user_id)  ON DELETE CASCADE,
    title_id      INTEGER NOT NULL REFERENCES titles(id)      ON DELETE CASCADE,
    subscribed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, title_id)
);

CREATE TABLE IF NOT EXISTS packs (
    id          SERIAL PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pack_titles (
    pack_id   INTEGER NOT NULL REFERENCES packs(id)   ON DELETE CASCADE,
    title_id  INTEGER NOT NULL REFERENCES titles(id)  ON DELETE CASCADE,
    PRIMARY KEY (pack_id, title_id)
);

CREATE TABLE IF NOT EXISTS delivery_log (
    id           SERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users(user_id)    ON DELETE CASCADE,
    edition_id   INTEGER NOT NULL REFERENCES editions(id)      ON DELETE CASCADE,
    delivered_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    status       TEXT    NOT NULL DEFAULT 'success'
                 CHECK (status IN ('success', 'failed'))
);

CREATE TABLE IF NOT EXISTS daily_scrape_status (
    id              SERIAL PRIMARY KEY,
    title_id        INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    date            DATE    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'found', 'failed')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMP WITH TIME ZONE,
    UNIQUE (title_id, date)
);

-- Create indices
CREATE INDEX IF NOT EXISTS idx_editions_title_date   ON editions(title_id, date);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user    ON subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_title   ON subscriptions(title_id);
CREATE INDEX IF NOT EXISTS idx_delivery_log_user     ON delivery_log(user_id);
CREATE INDEX IF NOT EXISTS idx_delivery_log_edition  ON delivery_log(edition_id);
CREATE INDEX IF NOT EXISTS idx_scrape_status_date    ON daily_scrape_status(date);
