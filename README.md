# 📰 Newsstand Bot

A full-featured Telegram bot for automated newspaper and magazine delivery. Subscribe to titles, get daily PDFs delivered to your chat, browse an archive of past editions.

## Features

- 📋 **50+ Indian newspapers** across 12 languages
- 📦 **Curated packs** — subscribe to bundles in one click
- ⏰ **Auto-delivery** — papers delivered every morning
- 🔄 **Smart retries** — keeps checking until papers are available
- 📚 **Archive** — request any past edition on demand
- 📊 **Read tracker** — weekly delivery stats
- 🗂️ **Telegram storage** — unlimited PDF archive via private channel
- 🚀 **Push-to-deploy** — edit config, git push, done

## Quick Start

### 1. Create a Telegram Bot

1. Open [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`, follow prompts
3. Copy the bot token

### 2. Create a Storage Channel

1. Create a new Telegram channel (private)
2. Add your bot as an admin (with "Post Messages" permission)
3. Get the channel ID:
   - Forward any message from the channel to [@userinfobot](https://t.me/userinfobot)
   - Or send a message in the channel, then check `https://api.telegram.org/bot<TOKEN>/getUpdates`

### 3. Get Your User ID

- Send `/start` to [@userinfobot](https://t.me/userinfobot) on Telegram
- Note your numeric user ID

### 4. Configure

```bash
# Clone the repo
git clone https://github.com/yourusername/newsstand-bot.git
cd newsstand-bot

# Create .env from template
cp .env.example .env

# Edit .env with your values
nano .env
```

Set these in `.env`:
```
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ
ADMIN_IDS=your_user_id
STORAGE_CHANNEL_ID=-1001234567890
```

### 5. Run Locally

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Run the bot
python main.py
```

### 6. Write Your Scraper

The bot ships with a scraper template. You need to fill in **one file** to connect it to your PDF source:

```bash
# Edit the template
nano scrapers/template_scraper.py
```

See [SCRAPER_GUIDE.md](scrapers/SCRAPER_GUIDE.md) for detailed instructions. The template has `TODO` markers at the 3 places you need to add code (~30 lines total).

**Test your scraper standalone:**
```bash
python -m scrapers.template_scraper
```

## Commands

### User Commands

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/help` | Full command reference |
| `/subscribe` | Browse & subscribe to titles by language |
| `/sub <title>` | Quick subscribe (fuzzy matched) |
| `/unsub <title>` | Quick unsubscribe |
| `/subscriptions` | View your active subscriptions |
| `/packs` | Browse curated title bundles |
| `/today` | Get all of today's available papers |
| `/get <title>` | Get today's edition of a specific title |
| `/get <title> DD-MM-YYYY` | Get a past edition from archive |
| `/tracker` | Weekly delivery statistics |
| `/lastupdated` | Last available date per title |

### Admin Commands

| Command | Description |
|---|---|
| `/upload` | Manually upload a PDF |
| `/sync` | Trigger scraper for all titles now |
| `/stats` | Bot statistics |
| `/broadcast <msg>` | Message all users |

## Configuration

All configuration lives in `config.yaml`. Edit this file to:

- **Add/remove titles** — add entries under `titles:`
- **Create packs** — add entries under `packs:`
- **Change schedule** — edit `schedule:` section
- **Manage languages** — titles are auto-grouped by their `language` field

After editing, just `git push` — the bot auto-deploys.

## Deployment (Oracle Cloud — Free Forever)

### One-Time Server Setup

1. **Create Oracle Cloud account** at [cloud.oracle.com](https://cloud.oracle.com)
2. **Create an Always Free ARM VM:**
   - Shape: VM.Standard.A1.Flex (1 OCPU, 6GB RAM)
   - Image: Ubuntu 22.04+
   - Add your SSH public key

3. **Set up the server:**

```bash
# SSH into VM
ssh -i ~/.ssh/your_key ubuntu@<VM_IP>

# Install Python
sudo apt update && sudo apt install python3-pip python3-venv git -y

# Clone your repo
cd ~
git clone https://github.com/yourusername/newsstand-bot.git
cd newsstand-bot

# Set up venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create .env
cp .env.example .env
nano .env  # fill in your values

# Test run
python main.py
```

4. **Create systemd service:**

```bash
sudo nano /etc/systemd/system/newsstand-bot.service
```

```ini
[Unit]
Description=Newsstand Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/newsstand-bot
EnvironmentFile=/home/ubuntu/newsstand-bot/.env
ExecStart=/home/ubuntu/newsstand-bot/venv/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable newsstand-bot
sudo systemctl start newsstand-bot
```

### Auto-Deploy via GitHub Actions

1. In your GitHub repo → Settings → Secrets, add:
   - `VM_HOST` — your VM's public IP
   - `VM_USER` — `ubuntu`
   - `SSH_KEY` — your private SSH key

2. Push to `main` branch → bot auto-deploys in ~15 seconds.

### Keeping the VM Alive

Oracle may reclaim idle VMs. To prevent this:

**Option A (recommended):** Upgrade to Pay-As-You-Go in OCI Console → Billing. Still free within limits, but exempt from idle reclaim.

**Option B:** Add a cron job:
```bash
crontab -e
# Add:
0 */6 * * * dd if=/dev/urandom bs=1M count=50 | md5sum > /dev/null 2>&1
```

## Project Structure

```
newsstand-bot/
├── main.py              # Entry point
├── config.yaml          # ⭐ All configuration here
├── config.py            # Config loader
├── requirements.txt     # Dependencies
├── .env.example         # Secrets template
├── .github/workflows/   # CI/CD
├── database/            # SQLite schema + operations
├── handlers/            # Bot command handlers
├── scrapers/            # Pluggable scraper system
├── delivery/            # Scheduler + delivery engine
└── utils/               # Helpers
```

## License

Personal use only. Respect copyright — use with content you have rights to access.
