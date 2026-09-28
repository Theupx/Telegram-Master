# Telegram Master

Multi-account Telegram automation built with **Telethon** and **aiogram**.

This repository contains the Serv00-ready edition of Telegram Master. The application manages persistent Telegram user sessions, provides a remote admin bot, supports phone and QR login flows, handles 2FA, saves media, tracks history, and optionally synchronizes persistent data to Google Drive.

## Structure

```text
.
├── telegram_master.py          # launcher
├── telegram_master/            # application package
│   ├── shared.py               # common imports
│   ├── core.py                 # storage + Telegram client core
│   ├── handlers.py             # Telethon event handlers
│   ├── remote.py               # remote admin bot / login
│   ├── account_info.py         # account cache + reporting
│   ├── flows.py                # account add flow
│   ├── cli.py                  # CLI / maintenance menus
│   └── main.py                 # application lifecycle
├── requirements.txt
├── requirements-drive.txt
├── .env.example
├── docs/
│   └── SERV00.md
└── scripts/
    └── start_serv00.sh
```

## Runtime storage

Runtime files stay outside the repository. The default location is:

```text
~/TelegramMaster/
```

This includes sessions, media, logs, backups, SQLite history, and runtime state.

## Configuration

Required environment variables:

```text
TELEGRAM_API_ID
TELEGRAM_API_HASH
TELEGRAM_BOT_TOKEN
TELEGRAM_REMOTE_BOT_TOKEN
TELEGRAM_REMOTE_ADMIN_IDS
```

Optional variables are documented in `.env.example`. Google Drive is an optional secondary persistence layer.

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python telegram_master.py
```

## Serv00

See `docs/SERV00.md` and `scripts/start_serv00.sh`.

## Security

Never commit Telegram `.session` files, `.env`, databases, private keys, or account credentials.

## Status

Current codebase: **Telegram Master V14.5-fixed18 — Serv00 edition**.
