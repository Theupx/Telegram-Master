TELEGRAM MASTER — SERV00 EDITION
=================================

حالت پیش‌فرض:
- ذخیره‌سازی محلی و پایدار داخل ~/TelegramMaster
- Google Drive اختیاری است
- منطق Telethon + aiogram حفظ شده است

Python / Virtualenv:
  mkdir -p ~/.virtualenvs
  virtualenv ~/.virtualenvs/telegram-master -p /usr/local/bin/python3.11
  ~/.virtualenvs/telegram-master/bin/pip install -r ~/TelegramMaster/requirements-serv00.txt

متغیرهای الزامی:
  TELEGRAM_API_ID
  TELEGRAM_API_HASH
  TELEGRAM_BOT_TOKEN
  TELEGRAM_REMOTE_BOT_TOKEN
  TELEGRAM_REMOTE_ADMIN_IDS

اختیاری:
  TELEGRAM_BACKUP_CHANNEL
  TELEGRAM_2FA_VAULT_KEY

برای اجرای دستی:
  cd ~/TelegramMaster
  ./start_serv00.sh

برای اجرای پس‌زمینه با screen:
  screen -dmS telegram-master ~/TelegramMaster/start_serv00.sh

ورود به screen:
  screen -r telegram-master

خروج بدون توقف:
  Ctrl+A سپس D

برای اجرای خودکار بعد از reboot، می‌توان cron را طوری تنظیم کرد که
screen و start_serv00.sh را اجرا کند.

Google Drive اختیاری:
  GOOGLE_DRIVE_ENABLED=1
  GOOGLE_DRIVE_FOLDER_ID=...
  GOOGLE_SERVICE_ACCOUNT_JSON=...
یا:
  GOOGLE_SERVICE_ACCOUNT_JSON_B64=...

مسیر داده پیش‌فرض:
  ~/TelegramMaster/

ساختار:
  00_SYSTEM/
  01_SESSIONS/
  02_MEDIA/
  03_BACKUPS/
  04_LOGS/
  05_RUNTIME/
