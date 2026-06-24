# 📰 Newsstand Bot

A Telegram bot that delivers Indian newspapers and international magazines to your chat. Subscribe to a title and new editions arrive automatically, or pull any recent edition on demand.

Newspapers and magazines are both **link-shares**: the bot scrapes the source, finds the download link (Google Drive for newspapers, mirror hosts for magazines) and sends you that link. It never downloads or re-hosts the files, so there's no Telegram storage channel and the hosting footprint stays tiny.

## Features

- **Browse to subscribe** — pick newspapers by language or search magazines by name
- **Automatic delivery** — new editions are pushed to subscribers as soon as they're found
- **On-demand fetch** — grab any newspaper edition from the last 30 days by date
- **Self-service management** — view and remove subscriptions inline
- **Web testing UI** — exercise the scrapers in a browser, no Telegram required

## Sources

| Type | Source | Catalogue |
|---|---|---|
| Newspapers | careerswave.in, dailyepaper.in (Google Drive links) | [`config.yaml`](config.yaml) |
| Magazines | downmagaz.net (searched on demand) | added when a user subscribes |

## Architecture

| Piece | Runs on | Role |
|---|---|---|
| Bot (commands, subscriptions, delivery) | **Render** web service (webhook) | Always-on, responds instantly |
| Database | **Supabase** (Postgres) | Titles, editions, subscriptions, delivery log |
| Scraping (newspapers + magazines) | **In-process** APScheduler on the bot (~15 min) | Primary scraper — polls so new editions land quickly |
| Scraping (backup) | **GitHub Actions** cron (every 15 min) | Runs the same cycle in case the bot host is down |

The bot stays awake on Render's free tier via a self-ping. Both newspapers and magazines are cheap link-lookups, so the always-on bot scrapes everything in-process every ~15 minutes — this is the reliable primary path, since GitHub Actions' cron is best-effort and frequently delayed or skipped. A catch-up pass at the end of every cycle re-sends any edition link a subscriber hasn't received yet, so a missed delivery self-heals on the next run.

### How a delivery happens
1. The scraper fetches the source page and extracts the download link for the target date.
2. The link is stored against the title and date in the `editions` table (deduplicated by date).
3. It's sent to every subscriber who hasn't received that edition yet (tracked in `delivery_log`).
4. Newspapers retry through the morning (after 6am IST) until the day's edition appears. Titles still missing after several attempts are reported to admins via DM.

## Tech stack

Python · [python-telegram-bot](https://docs.python-telegram-bot.org) · Supabase · APScheduler · BeautifulSoup · FastAPI (web UI) · Render · GitHub Actions

## Setup

### 1. Telegram
1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Get your numeric user id from [@userinfobot](https://t.me/userinfobot) for admin access (admins receive the daily failure report).

### 2. Supabase
1. Create a project at [supabase.com](https://supabase.com).
2. Run [`supabase_setup.sql`](supabase_setup.sql) in the SQL editor to create the schema.
3. Copy the project URL and an API key from **Project Settings → API**.

### 3. Configure & run
```bash
cp .env.example .env          # fill in BOT_TOKEN, ADMIN_IDS, SUPABASE_URL, SUPABASE_KEY
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```
With `WEBHOOK_URL` unset the bot runs in long-polling mode and the in-process scheduler stays off; scrape manually with `python run_scrapers.py`.

## Commands

| Command | What it does |
|---|---|
| `/start`, `/help` | Welcome and command reference |
| `/subscribe` | Interactive browser: subscribe to newspapers by language or search magazines by name |
| `/getlatest` | List your subscriptions; tap one to fetch its latest edition |
| `/unsubscribe` | List your subscriptions; tap one to remove it |
| `/get` | Fetch any edition on demand: a newspaper (pick a title → date) or a magazine (search → pick the magazine → pick an issue) |

Subscribing sends the latest available edition right away. If nothing is stored for that title yet, it's scraped on demand and delivered as soon as it's found.

## Configuration

Newspaper titles live in [`config.yaml`](config.yaml):
```yaml
titles:
  - name: The Times of India
    slug: the-times-of-india
    source_url: https://www.careerswave.in/times-of-india-epaper-pdf-free-download/
    language: English
    category: Newspaper
    scrape_website: careerswave_in   # module name in scrapers/
```
On startup the bot syncs these into the `titles` table and deactivates any newspaper no longer listed. Magazines aren't listed here — they're added when a user first subscribes.

## Deployment

### Bot → Render (primary scraper)
- New **Web Service** from this repo, start command `python main.py`.
- Env vars: `BOT_TOKEN`, `ADMIN_IDS`, `SUPABASE_URL`, `SUPABASE_KEY`, `WEBHOOK_URL` (`https://<service>.onrender.com/<BOT_TOKEN>`); optional `WEBHOOK_SECRET`, `PORT`, `SCRAPE_INTERVAL_MIN` (default 15).
- **`WEBHOOK_URL` must be set** — it switches the bot to webhook mode and starts the keep-alive ping plus the in-process scraper (which scrapes newspapers *and* magazines). Without it the bot polls and does no scheduled scraping.

### GitHub Actions (backup scraper)
[`.github/workflows/scrape.yml`](.github/workflows/scrape.yml) runs the same `run_scrapers.py` cycle every 15 minutes as a backup for when the bot host is down. Add repo secrets: `BOT_TOKEN`, `ADMIN_IDS`, `SUPABASE_URL`, `SUPABASE_KEY`. (Optional — you can disable it now that the bot scrapes in-process.)

## Web testing UI

A standalone FastAPI app ([`webui.py`](webui.py)) that drives the same config and scraper logic in a browser, useful for checking that sources still resolve. No database or bot token required.

```bash
pip install -r requirements-web.txt
python webui.py            # → http://127.0.0.1:8000
```
Choose **English** or **Hindi** to pick a newspaper from `config.yaml` and see its latest download link, or **Magazine** to search downmagaz.net and get the latest issue's links.

## Project structure
```
newsstand-bot/
├── main.py              # Entry: bot, webhook, in-process scraper scheduler
├── run_scrapers.py      # Scrape + delivery engine (CLI and importable cycle)
├── config.py            # Loads config.yaml + .env into typed settings
├── config.yaml          # Newspaper catalogue + schedule
├── database/
│   └── operations.py    # Supabase CRUD
├── handlers/            # /start, /subscribe, /getlatest, /unsubscribe, /get + callback router
├── scrapers/
│   ├── careerswave_in.py    # newspaper link finder
│   ├── dailyepaper_in.py    # newspaper link finder
│   └── downmagaz_net.py     # magazine search + link scraping
├── utils/
│   └── helpers.py       # dates, fuzzy matching, HTML escaping
├── webui.py + web/      # web testing UI (FastAPI)
└── supabase_setup.sql   # Database schema
```

## License

Personal use only. Respect publishers' copyright and only access content you're entitled to.
