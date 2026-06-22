# 📰 Newsstand Bot

A Telegram bot that delivers Indian newspapers and international magazines straight to your chat. Subscribe to titles, get new editions automatically, and fetch any recent edition on demand.

Both newspapers and magazines are **link-shares**: the bot sends the source download link (Google Drive for newspapers, mirror hosts for magazines) — it never downloads or re-hosts the files, so there's no Telegram storage channel.

- **Newspapers** — a curated list in [`config.yaml`](config.yaml), scraped from careerswave.in / dailyepaper.in (Google Drive links).
- **Magazines** — searched and scraped on demand from downmagaz.net.

## How it works

| Concern | Where it runs | Why |
|---|---|---|
| Bot (commands, subscriptions, delivery) | **Render** web service (webhook) | Always-on, responds instantly |
| Database (metadata: titles, editions, subs) | **Supabase** (Postgres) | Managed, free tier |
| Newspaper scraping | **GitHub Actions** cron | Finds the day's edition link |
| Magazine scraping | **In-process** APScheduler on Render | Delivered promptly, every ~15 min |

Magazines are polled in-process so new issues reach subscribers quickly instead of waiting on GitHub Actions' (best-effort, often-delayed) cron. A catch-up pass at the end of every cycle forwards any edition link a subscriber hasn't received yet.

## Setup

### 1. Telegram
1. Create a bot with [@BotFather](https://t.me/BotFather), copy the token.
2. Get your own numeric user id (via [@userinfobot](https://t.me/userinfobot)) for admin access (admins get the daily failure report).

### 2. Supabase
1. Create a project at [supabase.com](https://supabase.com).
2. In the SQL editor, run [`supabase_setup.sql`](supabase_setup.sql) to create the schema.
3. Copy the project URL and an API key from Project Settings → API.

### 3. Configure
```bash
cp .env.example .env
# fill in BOT_TOKEN, ADMIN_IDS, SUPABASE_URL, SUPABASE_KEY
```

### 4. Run locally (polling)
```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```
With `WEBHOOK_URL` unset the bot uses long-polling and the in-process scheduler stays off — run the scraper manually with `python run_scrapers.py`.

## Commands

| Command | Description |
|---|---|
| `/start`, `/help` | Welcome / command reference |
| `/subscribe` | Interactive browser: subscribe to newspapers (by language) or search magazines; tap a subscribed title again to unsubscribe |
| `/subscriptions` | View active subscriptions and remove any with a tap |
| `/get` | Fetch any newspaper edition on demand (pick title → date; scraped live, no archive needed) |

Subscribing delivers the latest available edition immediately; if nothing is stored yet, that one title is scraped on demand and delivered when it lands.

## Configuration

All newspaper titles live in [`config.yaml`](config.yaml). Each entry:
```yaml
- name: The Times of India
  slug: the-times-of-india
  source_url: https://www.careerswave.in/times-of-india-epaper-pdf-free-download/
  language: English
  category: Newspaper
  scrape_website: careerswave_in   # module in scrapers/
```
On startup the bot syncs these into the `titles` table and deactivates any newspaper no longer listed. Magazines are added dynamically when a user subscribes, so they are not listed here.

## Web testing UI

A standalone browser UI ([`webui.py`](webui.py)) for exercising the scraper/config logic without Telegram — handy for checking that sources still work.

```bash
pip install -r requirements-web.txt
python webui.py            # → http://127.0.0.1:8000
```

Pick **English** or **Hindi** to choose a newspaper from `config.yaml` and see its latest Google Drive link; pick **Magazine** to search downmagaz.net and get the latest available edition's download links. It reads `config.yaml` and calls the same scraper modules the bot uses — no database or bot token required.

## Deployment

### Bot → Render
- New **Web Service** from this repo, start command `python main.py`.
- Env vars: `BOT_TOKEN`, `ADMIN_IDS`, `SUPABASE_URL`, `SUPABASE_KEY`, `WEBHOOK_URL` (`https://<service>.onrender.com/<BOT_TOKEN>`), optionally `WEBHOOK_SECRET`, `PORT`, `MAGAZINE_SCRAPE_INTERVAL_MIN`.
- Setting `WEBHOOK_URL` switches the bot to webhook mode and enables the keep-alive self-ping + in-process magazine scraper.

### Newspaper scraper → GitHub Actions
[`.github/workflows/scrape.yml`](.github/workflows/scrape.yml) runs `run_scrapers.py` every 15 minutes. Add repo secrets: `BOT_TOKEN`, `ADMIN_IDS`, `SUPABASE_URL`, `SUPABASE_KEY`.

> If you'd rather not use GitHub Actions, you can run `run_scrapers.py` from any cron host with the same env vars. (Since newspapers are now light link-lookups, you could also move them onto the in-process scheduler.)

## Project structure
```
newsstand-bot/
├── main.py              # Entry point: bot, webhook, in-process magazine scheduler
├── run_scrapers.py      # Scrape + delivery engine (standalone CLI and importable cycle)
├── webui.py             # Standalone web testing UI (FastAPI)
├── web/index.html       # Test UI front-end
├── config.py / config.yaml
├── database/operations.py   # Supabase CRUD
├── handlers/            # /start /subscribe /subscriptions /get, callbacks
├── scrapers/            # careerswave_in, dailyepaper_in, downmagaz_net
├── utils/               # helpers, shared Google Drive downloader
└── supabase_setup.sql   # Schema
```

## License
Personal use only. Respect copyright — use only with content you have the right to access.
