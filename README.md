# Telegram Master

[![Python Check](https://github.com/Theupx/Telegram-Master/actions/workflows/python-check.yml/badge.svg)](https://github.com/Theupx/Telegram-Master/actions/workflows/python-check.yml)

> Multi-account Telegram automation built with **Telethon** and **aiogram**.

Telegram Master is a Telegram automation project focused on managing persistent user sessions, controlling multiple accounts, providing a remote admin interface, handling account login flows, saving media, tracking activity, and supporting optional cloud persistence.

## ✨ Features

* 👤 Multi-account Telegram session management
* 🤖 Remote admin control through a Telegram bot
* 📱 Phone-based account login
* 🔳 QR login support
* 🔐 Two-factor authentication with an encrypted local vault
* 🟢 Account activation / deactivation
* 📊 Account status and information
* 💾 Media saving with a custom reply command
* 📦 Backup-channel delivery with Bot API fallback
* 🗃️ Local SQLite history
* ☁️ Optional Google Drive synchronization
* 🔄 Retry and FloodWait handling
* 🧹 Graceful startup and shutdown
* 🖥️ Serv00-ready deployment

## 🧱 Architecture

```text
Telegram Master
│
├── Telethon
│   └── Telegram user accounts & sessions
│
├── aiogram
│   └── Remote admin bot
│
├── Local Storage
│   ├── Sessions
│   ├── Media
│   ├── Backups
│   ├── Logs
│   └── SQLite history
│
└── Optional Google Drive
    └── Secondary persistence / backup
```

## 📁 Project Structure

```text
.
├── telegram_master.py          # Application launcher
│
├── telegram_master/
│   ├── shared.py               # Shared utilities and imports
│   ├── core.py                 # Storage and Telegram client core
│   ├── handlers.py             # Telethon event handlers
│   ├── remote.py               # Remote admin bot and login UI
│   ├── account_info.py         # Account information and caching
│   ├── flows.py                # Account creation / login flows
│   ├── cli.py                  # CLI and maintenance tools
│   └── main.py                 # Application lifecycle
│
├── docs/
│   └── SERV00.md               # Serv00 deployment guide
│
├── scripts/
│   └── start_serv00.sh         # Serv00 startup script
│
├── requirements.txt             # Core dependencies
├── requirements-drive.txt       # Optional Google Drive dependencies
├── .env.example                 # Environment variable template
├── .gitignore
└── README.md
```

## 💾 Runtime Storage

Runtime data is intentionally kept outside the Git repository.

Default location:

```text
~/TelegramMaster/
```

The runtime directory may contain:

```text
00_SYSTEM/
01_SESSIONS/
02_MEDIA/
03_BACKUPS/
04_LOGS/
05_RUNTIME/
```

Authentication sessions and other runtime data should never be committed to Git.

## ⚙️ Configuration

Telegram Master uses environment variables for configuration.

### Required

```text
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_BOT_TOKEN=
TELEGRAM_REMOTE_BOT_TOKEN=
TELEGRAM_REMOTE_ADMIN_IDS=
```

### Optional

```text
TELEGRAM_BACKUP_CHANNEL=
TELEGRAM_2FA_VAULT_KEY=
TELEGRAM_DEVICE_MODEL=
TELEGRAM_SYSTEM_VERSION=
TELEGRAM_DATA_DIR=
```

### Google Drive

Google Drive is optional and can be enabled as a secondary persistence layer.

```text
GOOGLE_DRIVE_ENABLED=1
GOOGLE_DRIVE_FOLDER_ID=
GOOGLE_SERVICE_ACCOUNT_JSON=
```

See `.env.example` for the complete configuration template.

## 🚀 Local Development

Create a virtual environment:

```bash
python3 -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run:

```bash
python telegram_master.py
```

## 🌐 Serv00 Deployment

The project includes a dedicated Serv00 deployment guide:

```text
docs/SERV00.md
```

and a startup script:

```text
scripts/start_serv00.sh
```

The intended deployment keeps persistent application data inside the Serv00 home directory and runs the application as a background process.

## 🔐 Security

Telegram `.session` files are authentication credentials.

Never commit:

```text
.env
*.session
*.session-journal
*.db
*.sqlite
secure_vault.enc
private keys
service-account credentials
```

Keep all secrets in environment variables or other secure storage.

Only deploy this project with Telegram accounts and credentials that you are authorized to use.

## 🛠️ Development Status

**Current version:** `V14.5-fixed18 — Serv00 edition`

The application is currently kept as a modularized codebase while preserving the existing behavior of the original monolithic version.

Future development can continue around:

```text
├── testing
├── logging improvements
├── performance
├── deployment automation
└── further modularization
```

## 📌 Project History

The original monolithic implementation is kept under:

```text
archive/
```

This allows the current modular version to remain clean while preserving the earlier implementation for reference.

## 📄 License

MIT
