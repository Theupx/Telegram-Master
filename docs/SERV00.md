# Serv00 Deployment

Telegram Master stores its runtime data in persistent local storage by default.

## 1. Install Python environment

Create a virtual environment in your home directory:

```bash
mkdir -p ~/.virtualenvs
virtualenv ~/.virtualenvs/telegram-master -p /usr/local/bin/python3.11
```

Install the application dependencies:

```bash
~/.virtualenvs/telegram-master/bin/pip install -r ~/TelegramMaster/requirements.txt
```

Google Drive is optional. When enabled, install the extra dependencies:

```bash
~/.virtualenvs/telegram-master/bin/pip install -r ~/TelegramMaster/requirements-drive.txt
```

## 2. Environment variables

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

Google Drive (optional):

```text
GOOGLE_DRIVE_ENABLED=1
GOOGLE_DRIVE_FOLDER_ID=...
GOOGLE_SERVICE_ACCOUNT_JSON=...
```

or:

```text
GOOGLE_SERVICE_ACCOUNT_JSON_B64=...
```

Never commit real secrets to Git.

## 3. Application layout

```text
~/TelegramMaster/
├── 00_SYSTEM/
├── 01_SESSIONS/
├── 02_MEDIA/
├── 03_BACKUPS/
├── 04_LOGS/
└── 05_RUNTIME/
```

## 4. Start manually

```bash
cd ~/TelegramMaster
./scripts/start_serv00.sh
```

## 5. Run in the background

```bash
screen -dmS telegram-master ~/TelegramMaster/scripts/start_serv00.sh
```

Attach to the process:

```bash
screen -r telegram-master
```

Detach without stopping the application with `Ctrl+A`, then `D`.

## 6. Start after reboot

A cron entry can launch the application through `screen`, for example:

```cron
@reboot /usr/local/bin/bash -lc 'screen -dmS telegram-master $HOME/TelegramMaster/scripts/start_serv00.sh'
```
