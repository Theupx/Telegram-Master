#!/bin/sh
set -eu

APP_DIR="${TELEGRAM_APP_DIR:-$HOME/TelegramMaster}"
VENV_DIR="${TELEGRAM_VENV_DIR:-$HOME/.virtualenvs/telegram-master}"

if [ -f "$HOME/.bash_profile" ]; then
    . "$HOME/.bash_profile"
fi

cd "$APP_DIR"
exec "$VENV_DIR/bin/python" "$APP_DIR/telegram_master.py"
