# Telegram Private Channel File Forwarder

Watches a private Telegram channel you're a member of (**Channel A**) and
automatically **server-side forwards** newly posted files/media to another
private channel (**Channel B**) — using a real user account over MTProto
(via [Telethon](https://docs.telethon.dev/)), not the Bot API.

```
Private Channel A
      │
      ▼
Telegram User Account Session (MTProto)
      │
      ▼
      Telethon
      │
      ▼
Detect new files → forward (server-side) →  Private Channel B
```

## Why a user session, and no bot?

Telegram's `forwardMessages` call is **server-side**: Telegram copies the
message directly from one chat to another without the file ever passing
through your server. But the account issuing that call must be able to
*see* both chats. Since your bot is not a member of Channel A, a bot alone
cannot do this — it would have to download the file via the Bot API and
re-upload it, which is slow, uses your server's bandwidth/disk, and often
fails for large files.

Because the user account is already a member of Channel A **and** can post
in Channel B, a single Telethon client (authenticated as that user) can do
the whole job with one server-side call. That's the architecture this
project uses — no bot account is involved or required.

## Features

- 🔒 Reads Channel A using an authenticated **user session** (MTProto), never the Bot API
- ⚡ **Server-side forwarding** — no downloading/re-uploading, so large files and long videos work fine
- 🖼️ Groups multi-file **albums** and forwards them together as one group
- 🧠 **Deduplication** — a SQLite-backed record of forwarded message IDs means restarts never double-post
- ♻️ **Resumes after restarts** — catches up on anything posted while the service was offline
- 🔁 Automatic **reconnect** with exponential backoff on network drops
- 🌊 Proper **FloodWait** handling (sleeps exactly as long as Telegram asks, then retries)
- 🪵 Structured logging to console + rotating log file
- ⚙️ All secrets and channel IDs come from `.env` — nothing is hard-coded
- 🖥️ Includes a `systemd` unit for 24×7 operation on Ubuntu with auto-restart

## Project structure

```
telegram-channel-forwarder/
├── main.py                  # Entry point: reconnect loop, signal handling
├── forwarder/
│   ├── config.py             # Loads & validates .env configuration
│   ├── logger.py             # Console + rotating file logging setup
│   ├── storage.py            # SQLite dedupe / resume-state tracking
│   └── service.py            # Core logic: catch-up, live listen, forward, retries
├── scripts/
│   ├── generate_session.py   # One-time helper to create a SESSION_STRING
│   └── list_channels.py      # Lists visible chats + their numeric IDs
├── systemd/
│   └── telegram-forwarder.service
├── tests/
│   └── test_config.py
├── .env.example
├── .gitignore
├── requirements.txt
└── requirements-dev.txt
```

## Requirements

- Python 3.9+
- A Telegram **user account** that:
  - Is a member of Source Channel A (which must not have "Restrict Saving Content" enabled)
  - Can post in Destination Channel B
- `API_ID` / `API_HASH` from <https://my.telegram.org> → **API Development Tools**
- A Telethon `SESSION_STRING` for that account

## Installation

```bash
git clone <this-repo-url> telegram-channel-forwarder
cd telegram-channel-forwarder

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

### 1. Get `API_ID` and `API_HASH`

Log in at <https://my.telegram.org>, go to **API Development Tools**, and
create an app. Copy the `api_id` and `api_hash` into `.env`.

### 2. Generate a `SESSION_STRING`

If you don't already have one:

```bash
python scripts/generate_session.py
```

This logs in interactively (phone number, login code, 2FA password if
enabled) and prints a `SESSION_STRING` to paste into `.env`. **Never commit
or share this value** — it grants full access to the account, equivalent
to a password.

### 3. Find your channel IDs

Private channels usually have no `@username`, so you need the numeric chat
ID. With `SESSION_STRING` already in `.env`:

```bash
python scripts/list_channels.py
```

This prints every chat visible to the account with its ID — copy the ones
for Channel A and Channel B into `SOURCE_CHANNEL_ID` and `DEST_CHANNEL_ID`.

### 4. Fill in `.env`

At minimum:

```dotenv
API_ID=123456
API_HASH=your_api_hash
SESSION_STRING=your_session_string
SOURCE_CHANNEL_ID=-1001111111111
DEST_CHANNEL_ID=-1002222222222
```

See `.env.example` for every optional setting (retry limits, log level,
album buffering delay, etc.) with inline explanations.

## Running

```bash
source venv/bin/activate
python main.py
```

On first run, the service records the current latest message in Channel A
as a baseline and only forwards messages posted **after** that point — it
does not backfill the entire channel history by default. If you want the
existing backlog forwarded too on that first run, set
`FORWARD_EXISTING_ON_FIRST_RUN=true` in `.env` before starting it.

On every subsequent run (including after a crash or VPS reboot), the
service picks up exactly where it left off using its saved state — nothing
is missed, and nothing already forwarded is repeated.

Stop it with `Ctrl+C` (or `SIGTERM`) — it shuts down cleanly, flushing any
in-progress album buffer first.

## How it avoids duplicates and handles restarts

Every forwarded message's ID is recorded in a small SQLite database
(`data/forwarder.db` by default) together with the ID of the last message
processed for that channel. On startup, the service:

1. Reads the last processed message ID from SQLite.
2. Fetches anything posted after that ID (looping in pages until fully
   caught up, so it works even after long downtime).
3. Skips anything already marked as forwarded.
4. Starts listening live for new messages from that point forward.

## Deploying on an Ubuntu VPS (24×7)

1. **Copy the project to the server**, e.g. into `/opt/telegram-forwarder`:

   ```bash
   sudo mkdir -p /opt/telegram-forwarder
   sudo chown $USER:$USER /opt/telegram-forwarder
   git clone <this-repo-url> /opt/telegram-forwarder
   cd /opt/telegram-forwarder
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env
   nano .env   # fill in your values
   ```

2. **Create a dedicated system user** (recommended, avoids running as root):

   ```bash
   sudo useradd --system --home /opt/telegram-forwarder --shell /usr/sbin/nologin forwarder
   sudo chown -R forwarder:forwarder /opt/telegram-forwarder
   ```

3. **Install the systemd service**:

   ```bash
   sudo cp systemd/telegram-forwarder.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable telegram-forwarder
   sudo systemctl start telegram-forwarder
   ```

4. **Check status and logs**:

   ```bash
   sudo systemctl status telegram-forwarder
   sudo journalctl -u telegram-forwarder -f       # live systemd logs
   tail -f /opt/telegram-forwarder/logs/forwarder.log  # app log file
   ```

The unit file sets `Restart=always`, so the service comes back automatically
after a crash, an unexpected exit, or a VPS reboot (once `systemctl enable`
has been run). The app's own reconnect loop additionally handles transient
Telegram/network drops without needing systemd to restart the process at
all.

### Updating

```bash
cd /opt/telegram-forwarder
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart telegram-forwarder
```

## Configuration reference

All variables live in `.env` (see `.env.example` for defaults and inline
comments). The important ones:

| Variable | Required | Description |
|---|---|---|
| `API_ID`, `API_HASH` | ✅ | From my.telegram.org |
| `SESSION_STRING` | ✅ | Telethon user session — keep secret |
| `SOURCE_CHANNEL_ID` | ✅ | Numeric ID of Channel A |
| `DEST_CHANNEL_ID` | ✅ | Numeric ID of Channel B |
| `DROP_AUTHOR` | – | Hide "Forwarded from Channel A" label in B |
| `FORWARD_EXISTING_ON_FIRST_RUN` | – | Backfill history on first run |
| `CATCH_UP_LIMIT` | – | Page size when catching up after downtime |
| `MAX_RETRIES` | – | Retries for a failed forward before giving up on it |
| `FORWARD_DELAY_SECONDS` | – | Pause between forwards (politeness/rate-limit buffer) |
| `ALBUM_FLUSH_DELAY` | – | Debounce window for grouping albums |
| `LOG_LEVEL`, `LOG_FILE` | – | Logging configuration |
| `DB_PATH` | – | SQLite file for dedupe/resume state |

## Troubleshooting

**`ConfigError: Could not resolve SOURCE_CHANNEL_ID=...`**
The account either isn't a member of that chat, or the ID is wrong. Run
`python scripts/list_channels.py` to confirm the exact ID Telethon sees.

**Nothing gets forwarded**
Check the log file / `journalctl` output — every forward and every error is
logged. Confirm the account is a member of Channel A and can post in
Channel B, and that Channel A doesn't have "Restrict Saving Content" turned
on (server-side forwarding cannot bypass that restriction — this is a
Telegram-level protection, not something this tool can work around).

**`FloodWaitError` in the logs**
This is expected occasionally under heavy volume — the service sleeps for
exactly as long as Telegram specifies, then continues automatically.
Persistent flood waits usually mean `FORWARD_DELAY_SECONDS` should be
increased.

## Security notes

- `.env` is git-ignored by default — never commit it.
- `SESSION_STRING` is equivalent to a password for the account: anyone with
  it can log in as that user. Treat it like a secret (e.g. store it in your
  VPS's `.env` with restrictive file permissions: `chmod 600 .env`).
- Run the service as a dedicated, non-root system user (see deployment
  steps above).

## License

MIT — see [LICENSE](LICENSE).
