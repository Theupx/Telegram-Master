# Telegram Master

Telegram Master is a multi-account Telegram automation project built with **Telethon** and **aiogram**.

The project manages persistent Telegram user sessions, exposes a remote admin interface through a Telegram bot, supports account login and status management, saves media, keeps local history, and can optionally synchronize persistent data to Google Drive.

## Features

- Multi-account Telegram user-session management
- Remote admin control through an aiogram bot
- Phone login and QR login flows
- 2FA support with an encrypted local vault
- Session activation/deactivation
- Account status and management tools
- Media saving with a configurable reply command
- Backup-channel delivery with Bot API fallback
- Local SQLite history
- Persistent local storage
- Optional Google Drive secondary persistence
- Retry and FloodWait handling
- Graceful startup and shutdown

## Stack

- Python
- Telethon
- aiogram 3
- aiohttp
- SQLite
- cryptography
- qrcode
- Optional: Google Drive API

## Repository layout

```text
.
├── telegram_master.py
├── requirements.txt
├── requirements-drive.txt
├── .env.example
├── .gitignore
├── README.md
├── docs/
│   └── SERV00.md
└── scripts/
    └── start_serv00.sh
```

Runtime data is intentionally kept outside the repository. The default Serv00 location is:

```text
~/TelegramMaster/
```

## Configuration

Copy `.env.example` to your server environment and provide the required values.

Required:

```text
TELEGRAM_API_ID
TELEGRAM_API_HASH
TELEGRAM_BOT_TOKEN
TELEGRAM_REMOTE_BOT_TOKEN
TELEGRAM_REMOTE_ADMIN_IDS
```

Optional:

```text
TELEGRAM_BACKUP_CHANNEL
TELEGRAM_2FA_VAULT_KEY
TELEGRAM_DEVICE_MODEL
TELEGRAM_SYSTEM_VERSION
TELEGRAM_DATA_DIR
```

Google Drive is optional. Enable it only when needed:

```text
GOOGLE_DRIVE_ENABLED=1
GOOGLE_DRIVE_FOLDER_ID=...
GOOGLE_SERVICE_ACCOUNT_JSON=...
```

When Google Drive is enabled, install the additional dependencies from `requirements-drive.txt`.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python telegram_master.py
```

## Serv00

The repository includes a Serv00-specific startup script and setup guide:

```text
docs/SERV00.md
scripts/start_serv00.sh
```

For a background process, the intended pattern is to run the startup script inside `screen`.

## Security

Telegram `.session` files are authentication credentials. Never commit them to Git.

Never commit:

```text
.env
*.session
*.session-journal
*.db
*.sqlite*
secure_vault.enc
private keys
service-account credentials
```

Keep secrets in environment variables and keep runtime data outside the repository.

## Project status

Current codebase: **Telegram Master V14.5-fixed18 — Serv00 edition**.

The current application remains intentionally monolithic so the existing behavior can be preserved while deployment is stabilized. A later phase can split the code into modules without changing the external behavior.
