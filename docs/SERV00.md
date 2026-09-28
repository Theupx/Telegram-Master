# Serv00 Deployment

This project is prepared to run from persistent storage on Serv00.

## 1. Create the project directory

```bash
mkdir -p ~/TelegramMaster
```

Copy the repository files into that directory.

## 2. Create a virtual environment

```bash
mkdir -p ~/.virtualenvs
virtualenv ~/.virtualenvs/telegram-master -p /usr/local/bin/python3.11
```

Install the core dependencies:

```bash
~/.virtualenvs/telegram-master/bin/pip install -r ~/TelegramMaster/requirements.txt
```

For Google Drive support:

```bash
~/.virtualenvs/telegram-master/bin/pip install -r ~/TelegramMaster/requirements-drive.txt
```

## 3. Configure environment variables

Put the required environment variables in your shell profile, or provide them through your chosen process-management method.

Minimum configuration:

```bash
export TELEGRAM_API_ID="..."
export TELEGRAM_API_HASH="..."
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_REMOTE_BOT_TOKEN="..."
export TELEGRAM_REMOTE_ADMIN_IDS="123456789"
```

Do not place real credentials in the Git repository.

## 4. Run

```bash
~/TelegramMaster/scripts/start_serv00.sh
```

## 5. Run in the background

```bash
screen -dmS telegram-master ~/TelegramMaster/scripts/start_serv00.sh
```

Attach to it with:

```bash
screen -r telegram-master
```

Detach without stopping the process with `Ctrl+A`, then `D`.

## 6. Storage

By default the application stores persistent data in:

```text
~/TelegramMaster/
```

The application creates its runtime directories automatically.

## 7. Google Drive

Google Drive is no longer required for the Serv00 edition. Local persistent storage is the default.

To enable Drive as a secondary persistence layer:

```bash
export GOOGLE_DRIVE_ENABLED=1
export GOOGLE_DRIVE_FOLDER_ID="..."
export GOOGLE_SERVICE_ACCOUNT_JSON='...'
```

Install `requirements-drive.txt` before enabling it.
