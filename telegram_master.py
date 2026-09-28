# -*- coding: utf-8 -*-
"""
Telegram Master V14.5-fixed18 — Serv00 edition
Telethon user-account core + aiogram remote control + optional Google Drive persistence.
"""

import os
import json
import asyncio
import time
import shutil
import logging
import logging.handlers
import re
import sqlite3
import tempfile
import io
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from contextlib import contextmanager

import aiohttp
from cryptography.fernet import Fernet, InvalidToken
from telethon import TelegramClient, events, Button
from telethon.errors import (
    FloodWaitError,
    SessionPasswordNeededError,
    AuthKeyError,
    RPCError,
    PasswordHashInvalidError,
)

# ============================================================
# 0) GOOGLE DRIVE / PATHS
# ============================================================

BASE_DIR = Path(os.getenv("TELEGRAM_DATA_DIR", str(Path.home() / "TelegramMaster")))

SYSTEM_DIR = BASE_DIR / "00_SYSTEM"
SESSIONS_DIR = BASE_DIR / "01_SESSIONS"
MEDIA_DIR = BASE_DIR / "02_MEDIA"
MEDIA_TEMP_DIR = MEDIA_DIR / "TEMP"
MEDIA_SAVED_DIR = MEDIA_DIR / "SAVED"
BACKUPS_DIR = BASE_DIR / "03_BACKUPS"
SESSION_BACKUPS_DIR = BACKUPS_DIR / "SESSIONS"
MEDIA_BACKUPS_DIR = BACKUPS_DIR / "MEDIA"
LOG_DIR = BASE_DIR / "04_LOGS"
RUNTIME_DIR = BASE_DIR / "05_RUNTIME"
AUTH_TEMP_DIR = RUNTIME_DIR / "AUTH_TEMP"

CONFIG_FILE = SYSTEM_DIR / "config.json"
ACCOUNTS_FILE = SYSTEM_DIR / "accounts.json"
SETTINGS_FILE = SYSTEM_DIR / "settings.json"
HISTORY_DB = SYSTEM_DIR / "history.db"
MANIFEST_FILE = SYSTEM_DIR / "README.txt"
LOG_FILE = LOG_DIR / "bot.log"
VAULT_FILE = SYSTEM_DIR / "secure_vault.enc"

for folder in (
    BASE_DIR, SYSTEM_DIR, SESSIONS_DIR, MEDIA_DIR, MEDIA_TEMP_DIR,
    MEDIA_SAVED_DIR, BACKUPS_DIR, SESSION_BACKUPS_DIR, MEDIA_BACKUPS_DIR,
    LOG_DIR, RUNTIME_DIR, AUTH_TEMP_DIR,
):
    folder.mkdir(parents=True, exist_ok=True)

if not MANIFEST_FILE.exists():
    MANIFEST_FILE.write_text(
        "Telegram Master V14.2.1\n\n"
        "00_SYSTEM  = local configuration and account state\n"
        "01_SESSIONS = Telegram .session files\n"
        "02_MEDIA   = temporary and saved media\n"
        "03_BACKUPS = optional local backup copies\n"
        "04_LOGS    = rotating application logs\n"
        "05_RUNTIME = runtime-only application data\n"
        "AUTH_TEMP  = temporary login sessions removed unless auth succeeds\n\n"
        "Secrets are supplied only through environment variables.\n",
        encoding="utf-8",
    )

TEMP_DOWNLOAD_DIR = MEDIA_TEMP_DIR


# ============================================================
# 0.5) GOOGLE DRIVE PERSISTENCE
# ============================================================

class GoogleDriveSync:
    """Drive-backed persistence using a service-account-accessible folder."""
    SCOPES = ["https://www.googleapis.com/auth/drive"]
    FOLDER_MIME = "application/vnd.google-apps.folder"

    def __init__(self, root: Path):
        self.root = root
        self.folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
        self.credentials_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        self.credentials_b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_B64", "").strip()
        self.service = None
        self.enabled = False
        self._initialized = False
        self._dirty = True

    def mark_dirty(self, _path: Optional[Path] = None):
        self._dirty = True

    def _init_service(self) -> bool:
        if self._initialized:
            return self.enabled
        self._initialized = True
        if not self.folder_id:
            logger.warning("Google Drive disabled: GOOGLE_DRIVE_FOLDER_ID missing.")
            return False
        raw = self.credentials_json
        if not raw and self.credentials_b64:
            try:
                import base64
                raw = base64.b64decode(self.credentials_b64).decode("utf-8")
            except Exception:
                logger.exception("Could not decode GOOGLE_SERVICE_ACCOUNT_JSON_B64.")
                return False
        if not raw:
            logger.warning("Google Drive disabled: service-account credentials missing.")
            return False
        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
            info = json.loads(raw)
            creds = Credentials.from_service_account_info(info, scopes=self.SCOPES)
            self.service = build("drive", "v3", credentials=creds, cache_discovery=False)
            self.enabled = True
            logger.info("Google Drive persistence initialized.")
            return True
        except Exception:
            logger.exception("Google Drive initialization failed.")
            self.enabled = False
            return False

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def _find_child(self, parent_id: str, name: str, mime_type: Optional[str] = None):
        if not self._init_service():
            return None
        q = [f"'{parent_id}' in parents", "trashed = false", f"name = '{self._escape(name)}'"]
        if mime_type:
            q.append(f"mimeType = '{mime_type}'")
        result = self.service.files().list(q=" and ".join(q), fields="files(id,name,mimeType,modifiedTime)", pageSize=100).execute()
        rows = result.get("files", [])
        return rows[0] if rows else None

    def _ensure_folder(self, parent_id: str, name: str) -> str:
        existing = self._find_child(parent_id, name, self.FOLDER_MIME)
        if existing:
            return existing["id"]
        created = self.service.files().create(
            body={"name": name, "mimeType": self.FOLDER_MIME, "parents": [parent_id]},
            fields="id",
        ).execute()
        return created["id"]

    def _remote_parent(self, rel_parent: Path) -> str:
        parent = self.folder_id
        for part in rel_parent.parts:
            if part in ("", "."):
                continue
            parent = self._ensure_folder(parent, part)
        return parent

    def pull(self) -> bool:
        if not self._init_service():
            return False
        try:
            self._pull_folder(self.folder_id, Path("."))
            self._dirty = False
            logger.info("Google Drive sync-down completed.")
            return True
        except Exception:
            logger.exception("Google Drive sync-down failed.")
            return False

    def _pull_folder(self, parent_id: str, rel_dir: Path):
        from googleapiclient.http import MediaIoBaseDownload
        token = None
        while True:
            response = self.service.files().list(
                q=f"'{parent_id}' in parents and trashed = false",
                fields="nextPageToken,files(id,name,mimeType,modifiedTime)",
                pageSize=1000,
                pageToken=token,
            ).execute()
            for item in response.get("files", []):
                rel = rel_dir / item["name"]
                local = self.root / rel
                if item.get("mimeType") == self.FOLDER_MIME:
                    local.mkdir(parents=True, exist_ok=True)
                    self._pull_folder(item["id"], rel)
                    continue
                download = not local.exists()
                if local.exists():
                    try:
                        lm = datetime.fromtimestamp(local.stat().st_mtime, tz=timezone.utc)
                        rm = datetime.fromisoformat(item["modifiedTime"].replace("Z", "+00:00"))
                        download = rm > lm
                    except Exception:
                        download = False
                if download:
                    local.parent.mkdir(parents=True, exist_ok=True)
                    request = self.service.files().get_media(fileId=item["id"])
                    with local.open("wb") as fh:
                        downloader = MediaIoBaseDownload(fh, request, chunksize=8 * 1024 * 1024)
                        done = False
                        while not done:
                            _, done = downloader.next_chunk()
            token = response.get("nextPageToken")
            if not token:
                break

    def sync_up(self, force: bool = False) -> bool:
        if not force and not self._dirty:
            return True
        if not self._init_service():
            return False
        try:
            from googleapiclient.http import MediaFileUpload
            excluded = ("02_MEDIA/TEMP", "05_RUNTIME/AUTH_TEMP")
            for path in self.root.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(self.root).as_posix()
                if any(rel == x or rel.startswith(x + "/") for x in excluded):
                    continue
                parent = self._remote_parent(path.relative_to(self.root).parent)
                existing = self._find_child(parent, path.name)
                media = MediaFileUpload(str(path), resumable=True)
                if existing:
                    self.service.files().update(fileId=existing["id"], media_body=media).execute()
                else:
                    self.service.files().create(body={"name": path.name, "parents": [parent]}, media_body=media, fields="id").execute()
            self._dirty = False
            logger.info("Google Drive sync-up completed.")
            return True
        except Exception:
            logger.exception("Google Drive sync-up failed.")
            return False

drive_sync = GoogleDriveSync(BASE_DIR)


def print_drive_layout():
    """Show the storage layout once at startup."""
    print("\n╔════════════════════════════════════════════════════════════╗")
    print("║              TELEGRAM MASTER — STORAGE LAYOUT              ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print(f"📁 {BASE_DIR.name}/")
    print("├── 📁 00_SYSTEM/        config, accounts, settings, history")
    print("├── 📁 01_SESSIONS/      Telegram session files")
    print("├── 📁 02_MEDIA/")
    print("│   ├── 📁 TEMP/         temporary downloads")
    print("│   └── 📁 SAVED/        locally retained media")
    print("├── 📁 03_BACKUPS/")
    print("│   ├── 📁 SESSIONS/     optional local session backups")
    print("│   └── 📁 MEDIA/        optional local media backups")
    print("├── 📁 04_LOGS/          rotating logs")
    print("├── 📁 05_RUNTIME/       runtime-only files")
    print("│   └── 📁 AUTH_TEMP/    temporary login sessions")
    print("└── 📁 99_CODE/          project source code")
    print()

# ============================================================
# 1) LOGGING
# ============================================================

logger = logging.getLogger("TelegramMasterV14_5_fixed18_Serv00")
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

# ============================================================
# 2) RUNTIME SETTINGS
# ============================================================

MAX_CONCURRENT_CLIENTS = 5
MAX_INFO_CONCURRENT = 3

MAX_RETRY = 3
RETRY_DELAY = 5
HTTP_TIMEOUT = 120

REMOTE_STATE_TIMEOUT = 10 * 60
CACHE_TTL = 60

BOT_TOKEN: Optional[str] = None
REMOTE_BOT_TOKEN: Optional[str] = None
REMOTE_ADMIN_IDS: set[int] = set()
BACKUP_CHANNEL_INPUT: Optional[str] = None
API_ID: Optional[int] = None
API_HASH: Optional[str] = None
VAULT_KEY: Optional[bytes] = None
GOOGLE_DRIVE_ENABLED = False

_bot_session: Optional[aiohttp.ClientSession] = None

# ============================================================
# 3) SAFE CONFIG FILE
# ============================================================

class ConfigManager:
    @staticmethod
    def load(file_path: Path, default: Optional[dict] = None) -> dict:
        if default is None:
            default = {}

        if not file_path.exists():
            return dict(default)

        try:
            with file_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else dict(default)

        except json.JSONDecodeError as e:
            logger.error("JSON decode error in %s: %s", file_path, e)
            backup_path = file_path.with_suffix(file_path.suffix + ".corrupted_backup")
            try:
                shutil.copy2(file_path, backup_path)
            except OSError as backup_error:
                logger.warning("Could not preserve corrupted config: %s", backup_error)
            return dict(default)

        except OSError as e:
            logger.error("Error loading %s: %s", file_path, e)
            return dict(default)

    @staticmethod
    def save(file_path: Path, data: dict) -> bool:
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)

            fd, temp_name = tempfile.mkstemp(
                prefix=f".{file_path.name}.",
                suffix=".tmp",
                dir=str(file_path.parent),
            )

            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(
                        data,
                        f,
                        indent=4,
                        ensure_ascii=False,
                    )
                    f.flush()
                    os.fsync(f.fileno())

                os.replace(temp_name, file_path)

            finally:
                if os.path.exists(temp_name):
                    try:
                        os.remove(temp_name)
                    except OSError:
                        pass

            drive_sync.mark_dirty(file_path)
            return True

        except OSError as e:
            logger.error("Error saving %s: %s", file_path, e)
            return False


# ============================================================
# 4) SQLITE HISTORY
# ============================================================

class HistoryStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    sender_name TEXT,
                    sender_username TEXT,
                    phone TEXT,
                    file_name TEXT,
                    file_size_bytes INTEGER,
                    media_type TEXT,
                    caption_snippet TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_timestamp "
                "ON history(timestamp)"
            )
            conn.commit()

    def add(self, entry: dict):
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO history (
                    timestamp,
                    sender_name,
                    sender_username,
                    phone,
                    file_name,
                    file_size_bytes,
                    media_type,
                    caption_snippet
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.get("timestamp"),
                    entry.get("sender_name"),
                    entry.get("sender_username"),
                    entry.get("phone"),
                    entry.get("file_name"),
                    entry.get("file_size_bytes", 0),
                    entry.get("media_type"),
                    entry.get("caption_snippet"),
                ),
            )
            conn.commit()
            drive_sync.mark_dirty(self.db_path)

    def last(self, limit: int = 10) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT timestamp, sender_name, sender_username, phone,
                       file_name, file_size_bytes, media_type, caption_snippet
                FROM history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [dict(row) for row in rows]

history_store = HistoryStore(HISTORY_DB)

# ============================================================
# 5) SETTINGS MANAGER
# ============================================================

class SettingsManager:
    def __init__(self):
        self._active_status: Dict[str, bool] = {}
        self._load()

    def _load(self):
        data = ConfigManager.load(SETTINGS_FILE, {"accounts": {}})
        self._active_status = {}

        for name, info in data.get("accounts", {}).items():
            self._active_status[name] = bool(info.get("active", True))

    def save(self):
        data = {
            "accounts": {
                name: {"active": active}
                for name, active in self._active_status.items()
            }
        }
        ConfigManager.save(SETTINGS_FILE, data)

    def get(self, session_name: str, default: bool = True) -> bool:
        return self._active_status.get(session_name, default)

    def set(self, session_name: str, active: bool):
        self._active_status[session_name] = bool(active)
        self.save()

    def toggle(self, session_name: str) -> bool:
        new_status = not self.get(session_name)
        self.set(session_name, new_status)
        return new_status

    def remove(self, session_name: str):
        if session_name in self._active_status:
            del self._active_status[session_name]
            self.save()

    def sync_with_sessions(self):
        session_names = get_session_files()
        changed = False

        for name in session_names:
            if name not in self._active_status:
                self._active_status[name] = True
                changed = True

        stale = [n for n in self._active_status if n not in session_names]
        for name in stale:
            del self._active_status[name]
            changed = True

        if changed:
            self.save()

        return dict(self._active_status)

settings_manager = SettingsManager()

# ============================================================
# 6) TELEGRAM CLIENT MANAGER
# ============================================================

class ClientManager:
    def __init__(self):
        self.clients: Dict[str, TelegramClient] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.lock = asyncio.Lock()

    def get(self, name: str) -> Optional[TelegramClient]:
        return self.clients.get(name)

    def names(self) -> List[str]:
        return list(self.clients.keys())

    async def add_running_client(
        self,
        name: str,
        client: TelegramClient,
    ):
        async with self.lock:
            self.clients[name] = client

            task = asyncio.create_task(
                self._run_client(name, client),
                name=f"telegram-client-{name}",
            )
            self.tasks[name] = task

    async def _run_client(self, name: str, client: TelegramClient):
        try:
            await client.run_until_disconnected()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Client task crashed for %s", name)
        finally:
            async with self.lock:
                current = self.clients.get(name)
                if current is client:
                    self.clients.pop(name, None)

                task = self.tasks.get(name)
                current_task = asyncio.current_task()
                if task is current_task:
                    self.tasks.pop(name, None)

    async def remove(self, name: str):
        async with self.lock:
            client = self.clients.pop(name, None)
            task = self.tasks.pop(name, None)

        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        if client:
            try:
                await client.disconnect()
            except Exception as e:
                logger.warning("Disconnect error for %s: %s", name, e)

    async def stop_all(self):
        names = self.names()
        if names:
            await asyncio.gather(
                *(self.remove(name) for name in names),
                return_exceptions=True,
            )

client_manager = ClientManager()

# ============================================================
# 7) REMOTE STATE
# ============================================================

remote_state: Dict[int, dict] = {}
remote_state_lock = asyncio.Lock()

async def clear_remote_state(chat_id: int):
    async with remote_state_lock:
        state = remote_state.pop(chat_id, None)

    if not state:
        return

    client = state.get("client")
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass

    if state.get("temp_session"):
        session_name = state.get("session_name")
        if session_name:
            delete_temp_session_files(session_name)

async def cleanup_expired_remote_states():
    now = time.monotonic()
    expired = []

    async with remote_state_lock:
        for chat_id, state in remote_state.items():
            if now - state.get("last_activity", now) > REMOTE_STATE_TIMEOUT:
                expired.append(chat_id)

    for chat_id in expired:
        await clear_remote_state(chat_id)
        logger.info("Expired remote state for chat_id=%s", chat_id)

    purge_stale_auth_temp_sessions()

# ============================================================
# 8) DEVICE DATA
# ============================================================

DEVICE_MODEL = os.getenv("TELEGRAM_DEVICE_MODEL", "TelegramMaster Serv00")
SYSTEM_VERSION = os.getenv("TELEGRAM_SYSTEM_VERSION", "Linux")

def get_device_model(_filename_hash: str) -> Tuple[str, str]:
    return DEVICE_MODEL, SYSTEM_VERSION


def generate_session_filename(phone: str) -> str:
    return f"{int(time.time())}_{phone}"

# ============================================================
# 9) SESSION FILE HELPERS
# ============================================================

def get_session_files() -> List[str]:
    if not SESSIONS_DIR.exists():
        return []

    return sorted(
        p.stem
        for p in SESSIONS_DIR.iterdir()
        if p.is_file() and p.name.endswith(".session")
    )


def delete_session_files(session_name: str):
    for ext in (".session", ".session-journal"):
        path = SESSIONS_DIR / f"{session_name}{ext}"
        try:
            if path.exists():
                path.unlink()
                logger.info("Deleted session file: %s", path)
        except OSError as e:
            logger.warning("Could not delete %s: %s", path, e)


def delete_temp_session_files(session_name: str):
    for ext in (".session", ".session-journal"):
        path = AUTH_TEMP_DIR / f"{session_name}{ext}"
        try:
            if path.exists():
                path.unlink()
                logger.info("Deleted temporary auth session: %s", path)
        except OSError as e:
            logger.warning("Could not delete temporary session %s: %s", path, e)


def promote_authenticated_session(session_name: str) -> bool:
    temp_files = [
        AUTH_TEMP_DIR / f"{session_name}.session",
        AUTH_TEMP_DIR / f"{session_name}.session-journal",
    ]
    existing = [p for p in temp_files if p.exists()]

    if not (AUTH_TEMP_DIR / f"{session_name}.session").exists():
        logger.error("Temporary authenticated session is missing: %s", session_name)
        return False

    final_session = SESSIONS_DIR / f"{session_name}.session"
    final_journal = SESSIONS_DIR / f"{session_name}.session-journal"

    try:
        if final_session.exists():
            logger.error("Refusing to overwrite existing session: %s", final_session)
            delete_temp_session_files(session_name)
            return False

        tmp_final = SESSIONS_DIR / f".{session_name}.promote.tmp"
        shutil.copy2(AUTH_TEMP_DIR / f"{session_name}.session", tmp_final)
        os.replace(tmp_final, final_session)

        temp_journal = AUTH_TEMP_DIR / f"{session_name}.session-journal"
        if temp_journal.exists():
            tmp_journal = SESSIONS_DIR / f".{session_name}.journal.promote.tmp"
            shutil.copy2(temp_journal, tmp_journal)
            os.replace(tmp_journal, final_journal)

        for path in existing:
            try:
                path.unlink()
            except OSError:
                pass

        drive_sync.mark_dirty(final_session)
        logger.info("Authenticated session promoted to 01_SESSIONS: %s", session_name)
        return True

    except Exception:
        logger.exception("Failed to promote authenticated session: %s", session_name)
        for leftover in (
            SESSIONS_DIR / f".{session_name}.promote.tmp",
            SESSIONS_DIR / f".{session_name}.journal.promote.tmp",
        ):
            try:
                if leftover.exists():
                    leftover.unlink()
            except OSError:
                pass
        delete_session_files(session_name)
        return False


def purge_stale_auth_temp_sessions(max_age_seconds: int = REMOTE_STATE_TIMEOUT):
    now = time.time()
    for path in AUTH_TEMP_DIR.glob("*.session"):
        try:
            if now - path.stat().st_mtime > max_age_seconds:
                delete_temp_session_files(path.stem)
        except OSError:
            pass

# ============================================================
# 10) ACCOUNTS STORAGE
# ============================================================

def load_accounts() -> dict:
    data = ConfigManager.load(ACCOUNTS_FILE, {})
    changed = False
    for _name, info in data.items():
        if not isinstance(info, dict):
            continue
        if "two_fa" in info:
            info.pop("two_fa", None)
            info["two_fa_enabled"] = True
            changed = True
        elif "two_fa_enabled" not in info:
            info["two_fa_enabled"] = False
            changed = True
    if changed:
        ConfigManager.save(ACCOUNTS_FILE, data)
    return data

def save_accounts(accounts: dict) -> bool:
    return ConfigManager.save(ACCOUNTS_FILE, accounts)

def normalize_phone(value: str) -> str:
    return re.sub(r"\D", "", value or "")

def find_account_by_phone(phone: str) -> Optional[Tuple[str, dict]]:
    target = normalize_phone(phone)
    if not target:
        return None
    for session_name, info in load_accounts().items():
        if info.get("logged_out"):
            continue
        if normalize_phone(str(info.get("phone", ""))) == target:
            return session_name, info
    return None

def upsert_account(session_name: str, data: dict):
    accounts = load_accounts()
    accounts[session_name] = data
    save_accounts(accounts)

def remove_account(session_name: str):
    accounts = load_accounts()
    if session_name in accounts:
        del accounts[session_name]
        save_accounts(accounts)

# ============================================================
# 11) SECRETS / COLAB
# ============================================================

def load_runtime_secrets():
    global BOT_TOKEN, REMOTE_BOT_TOKEN, REMOTE_ADMIN_IDS, BACKUP_CHANNEL_INPUT
    global API_ID, API_HASH, GOOGLE_DRIVE_ENABLED

    api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    remote_token = os.getenv("TELEGRAM_REMOTE_BOT_TOKEN", "").strip()
    admin_ids_raw = os.getenv("TELEGRAM_REMOTE_ADMIN_IDS", "").strip()
    backup_channel_raw = os.getenv("TELEGRAM_BACKUP_CHANNEL", "").strip()
    vault_key_raw = os.getenv("TELEGRAM_2FA_VAULT_KEY", "").strip()

    if not api_id_raw or not api_hash or not bot_token or not remote_token:
        raise RuntimeError(
            "Missing required Telegram environment variables."
        )

    try:
        API_ID = int(api_id_raw)
    except (TypeError, ValueError):
        raise ValueError("TELEGRAM_API_ID is invalid.")

    API_HASH = api_hash
    BOT_TOKEN = bot_token
    REMOTE_BOT_TOKEN = remote_token
    BACKUP_CHANNEL_INPUT = backup_channel_raw or None

    global VAULT_KEY
    if vault_key_raw:
        try:
            candidate_key = vault_key_raw.encode("ascii")
            Fernet(candidate_key)
            VAULT_KEY = candidate_key
            logger.info("2FA vault key loaded.")
        except Exception as exc:
            raise ValueError(
                "TELEGRAM_2FA_VAULT_KEY is invalid. Generate a Fernet key."
            ) from exc
    else:
        VAULT_KEY = None
        logger.warning(
            "2FA vault key not configured; 2FA passwords will not be stored."
        )

    REMOTE_ADMIN_IDS = set()
    for raw in admin_ids_raw.split(","):
        raw = raw.strip()
        if raw:
            try:
                REMOTE_ADMIN_IDS.add(int(raw))
            except ValueError:
                logger.warning("Ignoring invalid admin ID environment value.")

    if not REMOTE_ADMIN_IDS:
        raise RuntimeError(
            "TELEGRAM_REMOTE_ADMIN_IDS must contain at least one numeric Telegram user ID."
        )

    # Serv00 keeps persistent application data locally.
    # Google Drive remains available as an optional secondary backup.
    drive_flag = os.getenv("GOOGLE_DRIVE_ENABLED", "0").strip().lower()
    GOOGLE_DRIVE_ENABLED = drive_flag in {"1", "true", "yes", "on"}

    drive_sync.folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    drive_sync.credentials_json = os.getenv(
        "GOOGLE_SERVICE_ACCOUNT_JSON", ""
    ).strip()
    drive_sync.credentials_b64 = os.getenv(
        "GOOGLE_SERVICE_ACCOUNT_JSON_B64", ""
    ).strip()

    if GOOGLE_DRIVE_ENABLED:
        if not drive_sync.folder_id or not (
            drive_sync.credentials_json or drive_sync.credentials_b64
        ):
            raise RuntimeError(
                "GOOGLE_DRIVE_ENABLED=1 requires GOOGLE_DRIVE_FOLDER_ID "
                "and service-account credentials."
            )
        logger.info("Google Drive secondary persistence enabled.")
    else:
        logger.info("Serv00 local persistence enabled; Google Drive sync is OFF.")


# ============================================================
# 12) HTTP SESSION / BOT API
# ============================================================

async def get_bot_session() -> aiohttp.ClientSession:
    global _bot_session

    if _bot_session is None or _bot_session.closed:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        _bot_session = aiohttp.ClientSession(timeout=timeout)

    return _bot_session

async def close_bot_session():
    global _bot_session

    if _bot_session and not _bot_session.closed:
        await _bot_session.close()

    _bot_session = None

async def validate_channel_via_bot(channel_input: str) -> bool:
    if not BOT_TOKEN:
        return False

    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat"
        session = await get_bot_session()

        async with session.post(
            url,
            json={"chat_id": channel_input},
        ) as resp:
            if resp.status == 200:
                return True

            text = await resp.text()
            logger.warning("Bot getChat failed: HTTP %s - %s", resp.status, text[:300])
            return False

    except Exception as e:
        logger.warning("validate_channel_via_bot error: %s", e)
        return False

async def send_via_bot(
    file_path: str,
    caption: str,
    chat_id: Any,
    retry: int = 2,
) -> bool:
    if not BOT_TOKEN:
        return False

    for attempt in range(1, retry + 1):
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
            session = await get_bot_session()

            data = aiohttp.FormData()
            data.add_field("chat_id", str(chat_id))
            data.add_field("caption", caption)

            with open(file_path, "rb") as f:
                data.add_field(
                    "document",
                    f,
                    filename=os.path.basename(file_path),
                )

                async with session.post(url, data=data) as resp:
                    if resp.status == 200:
                        logger.info("File sent via Bot API successfully.")
                        return True

                    response_text = await resp.text()
                    logger.error(
                        "Bot API error HTTP %s: %s",
                        resp.status,
                        response_text[:500],
                    )

                    if resp.status in (400, 403):
                        return False

        except asyncio.TimeoutError:
            logger.warning("Bot upload timeout on attempt %s", attempt)

        except OSError as e:
            logger.error("Could not read upload file: %s", e)
            return False

        except Exception:
            logger.exception("Bot upload error on attempt %s", attempt)

        if attempt < retry:
            delay = RETRY_DELAY * (2 ** (attempt - 1))
            await asyncio.sleep(min(delay, 30))

    return False

# ============================================================
# 13) BACKUP CHANNEL
# ============================================================

backup_channel_entity = None
backup_channel_input_cached = None

def get_configured_backup_channel() -> Optional[str]:
    if BACKUP_CHANNEL_INPUT:
        return str(BACKUP_CHANNEL_INPUT).strip() or None

    config = ConfigManager.load(CONFIG_FILE)
    value = config.get("backup_channel")
    if value is None:
        return None
    value = str(value).strip()
    return value or None

async def get_backup_channel(
    client: TelegramClient,
) -> Optional[Any]:
    global backup_channel_entity
    global backup_channel_input_cached

    channel_input = get_configured_backup_channel()

    if not channel_input:
        return None

    channel_input = str(channel_input).strip()

    if (
        backup_channel_entity is not None
        and backup_channel_input_cached == channel_input
    ):
        return backup_channel_entity

    try:
        if channel_input.lstrip("-").isdigit():
            entity = await client.get_entity(int(channel_input))
        else:
            entity = await client.get_entity(channel_input)

        backup_channel_entity = entity
        backup_channel_input_cached = channel_input

        return entity

    except Exception as e:
        logger.warning("Could not resolve backup channel: %s", e)
        return None

async def send_to_backup_channel(
    client: TelegramClient,
    file_path: str,
    caption: str,
    retry: int = MAX_RETRY,
) -> bool:
    if not os.path.exists(file_path):
        logger.error("Backup file not found: %s", file_path)
        return False

    channel_input = get_configured_backup_channel()

    if not channel_input:
        logger.warning("Backup channel is not configured.")
        return False

    channel_input = str(channel_input).strip()

    try:
        chat_id = (
            int(channel_input)
            if channel_input.lstrip("-").isdigit()
            else channel_input
        )
    except (TypeError, ValueError):
        chat_id = channel_input

    entity = await get_backup_channel(client)

    if entity is not None:
        for attempt in range(1, retry + 1):
            try:
                await client.send_file(
                    entity,
                    file_path,
                    caption=caption,
                    force_document=False,
                )
                logger.info(
                    "Backup sent through user client: attempt %s",
                    attempt,
                )
                return True

            except FloodWaitError as e:
                logger.warning(
                    "FloodWait while backing up: %s seconds",
                    e.seconds,
                )
                await asyncio.sleep(e.seconds)

            except Exception as e:
                logger.warning(
                    "Backup attempt %s failed: %s",
                    attempt,
                    e,
                )

                error_text = str(e)
                access_error = any(
                    marker in error_text
                    for marker in (
                        "Invalid channel object",
                        "CHAT_ADMIN_REQUIRED",
                        "USER_NOT_PARTICIPANT",
                        "Could not find the input entity",
                    )
                )

                if access_error:
                    if await send_via_bot(
                        file_path,
                        caption,
                        chat_id,
                    ):
                        return True

            if attempt < retry:
                await asyncio.sleep(
                    min(RETRY_DELAY * (2 ** (attempt - 1)), 60)
                )

    return await send_via_bot(
        file_path,
        caption,
        chat_id,
        retry=MAX_RETRY,
    )

# ============================================================
# 14) GENERAL HELPERS
# ============================================================

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

async def async_input(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)

def create_progress_callback():
    last_percent = [-1]

    def callback(current, total):
        if total > 0:
            pct = int((current / total) * 100)

            if pct % 10 == 0 and pct != last_percent[0]:
                logger.info("Progress: %s%%", pct)
                last_percent[0] = pct

    return callback

def sanitize_display_name(value: str) -> str:
    return (
        value.replace(" ", "_")
        .replace(".", "")
        .replace(",", "")
        .replace("\n", "_")
        .replace("\r", "_")
    )

# ============================================================
# 15) HISTORY COMPATIBILITY
# ============================================================

def log_file_history(entry: dict):
    try:
        history_store.add(entry)
    except Exception:
        logger.exception("Failed to write history")

# ============================================================
# 16) عجب / .history
# ============================================================

def setup_bot_handlers(
    client: TelegramClient,
    session_name: str,
):
    @client.on(events.NewMessage(pattern=r"^عجب$"))
    async def ajab_handler(event):
        if not event.out:
            return

        reply = await event.get_reply_message()

        if not reply:
            try:
                await event.delete()
            except Exception:
                pass
            return

        if not reply.media:
            try:
                await event.delete()
            except Exception:
                pass
            return

        if not (
            hasattr(reply.media, "document")
            or hasattr(reply.media, "photo")
        ):
            try:
                await event.delete()
            except Exception:
                pass
            return

        file_path = None

        try:
            sender = await event.get_sender()

            first_name = getattr(sender, "first_name", "") or ""
            last_name = getattr(sender, "last_name", "") or ""

            full_name = (
                f"{first_name} {last_name}".strip()
                or "Unknown"
            )

            username = (
                f"@{sender.username}"
                if getattr(sender, "username", None)
                else "No username"
            )

            phone = getattr(sender, "phone", None) or "Unknown"

            if reply.photo:
                media_type = "photo"
            elif reply.video:
                media_type = "video"
            elif reply.document:
                media_type = "document"
            else:
                media_type = "media"

            progress_cb = create_progress_callback()

            file_path = await client.download_media(
                reply,
                file=str(TEMP_DOWNLOAD_DIR),
                progress_callback=progress_cb,
            )

            if not file_path:
                await event.edit("❌ دانلود ناموفق بود.")
                return

            timestamp = datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            safe_name = sanitize_display_name(full_name)

            hashtags = (
                f"#{safe_name} "
                f"#date_{datetime.now().strftime('%Y-%m-%d')} "
                f"#{media_type}"
            )

            caption = (
                f"📁 {full_name} ({username})\n"
                f"📞 {phone}\n"
                f"🕒 {timestamp}\n"
                f"{hashtags}"
            )

            if reply.text:
                caption += f"\n📄 {reply.text[:200]}"

            backup_ok = await send_to_backup_channel(
                client,
                file_path,
                caption,
            )

            if not backup_ok:
                logger.error(
                    "Backup channel upload failed for %s",
                    os.path.basename(file_path),
                )

            await client.send_file(
                "me",
                file_path,
                caption=(
                    f"File saved from "
                    f"{full_name} ({username})"
                ),
                force_document=False,
            )

            try:
                file_size = os.path.getsize(file_path)
            except OSError:
                file_size = 0

            log_entry = {
                "timestamp": timestamp,
                "sender_name": full_name,
                "sender_username": username,
                "phone": phone,
                "file_name": os.path.basename(file_path),
                "file_size_bytes": file_size,
                "media_type": media_type,
                "caption_snippet": (
                    reply.text or ""
                )[:100],
            }

            log_file_history(log_entry)

            await event.delete()

            logger.info(
                "File saved successfully for session %s",
                session_name,
            )

        except FloodWaitError as e:
            logger.warning(
                "FloodWait in عجب for %s seconds",
                e.seconds,
            )
            try:
                await event.delete()
            except Exception:
                pass

        except Exception:
            logger.exception(
                "Error in ajab_handler for %s",
                session_name,
            )
            try:
                await event.delete()
            except Exception:
                pass

        finally:
            if file_path:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        logger.info(
                            "Temporary file removed: %s",
                            file_path,
                        )
                except OSError as e:
                    logger.warning(
                        "Could not remove temp file %s: %s",
                        file_path,
                        e,
                    )

    @client.on(events.NewMessage(pattern=r"^\.history$"))
    async def history_handler(event):
        if not event.out:
            return

        try:
            sender = await event.get_sender()
            sender_id = int(getattr(sender, "id", 0) or 0)
            if sender_id not in REMOTE_ADMIN_IDS:
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            history = history_store.last(10)

            if not history:
                await event.edit(
                    "📭 تاریخچه‌ای وجود ندارد."
                )
                return

            text = "📋 **آخرین فایل‌های ذخیره‌شده:**\n\n"

            for i, entry in enumerate(history, 1):
                text += (
                    f"{i}. {entry.get('file_name', 'Unknown')} "
                    f"({entry.get('media_type', 'media')})\n"
                    f"   از {entry.get('sender_name', 'Unknown')} "
                    f"- {entry.get('timestamp', 'Unknown')}\n\n"
                )

            await event.edit(text[:4000])

        except Exception:
            logger.exception("Error reading history")
            try:
                await event.delete()
            except Exception:
                pass

# ============================================================
# 17) SESSION BACKUP
# ============================================================

async def backup_session_on_login(
    session_name: str,
    phone: str,
    api_id: int,
    api_hash: str,
) -> bool:
    session_file = SESSIONS_DIR / f"{session_name}.session"

    if not session_file.exists():
        logger.error("Final session file not found for backup: %s", session_file)
        return False

    client = create_telegram_client(
        session_name,
        api_id,
        api_hash,
        temporary=False,
    )

    try:
        await client.connect()

        if not await client.is_user_authorized():
            logger.error("Refusing to back up unauthorized session: %s", session_name)
            return False

        me = await client.get_me()
        first_name = me.first_name or ""
        last_name = me.last_name or ""
        full_name = f"{first_name} {last_name}".strip() or "Unknown"
        username = f"@{me.username}" if me.username else "No username"
        phone_value = me.phone or phone
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        caption = (
            f"👤 {full_name}\n"
            f"📱 {username}\n"
            f"📞 {phone_value}\n"
            f"🕒 Session created: {timestamp}"
        )

        success = await send_to_backup_channel(
            client,
            str(session_file),
            caption,
        )

        if success:
            logger.info("Session backup sent for %s", session_name)
        else:
            logger.error("Failed to backup session for %s", session_name)

        return success

    except Exception:
        logger.exception("Error backing up session %s", session_name)
        return False

    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

# ============================================================
# 18) CLIENT CREATION
# ============================================================

def create_telegram_client(
    session_name: str,
    api_id: int,
    api_hash: str,
    temporary: bool = False,
) -> TelegramClient:
    base_dir = AUTH_TEMP_DIR if temporary else SESSIONS_DIR
    device_model, os_version = get_device_model(session_name)

    return TelegramClient(
        str(base_dir / session_name),
        api_id,
        api_hash,
        device_model=device_model,
        system_version=os_version,
    )


def create_auth_temp_client(
    session_name: str,
    api_id: int,
    api_hash: str,
) -> TelegramClient:
    return create_telegram_client(
        session_name,
        api_id,
        api_hash,
        temporary=True,
    )

# ============================================================
# 19) START / STOP BOT
# ============================================================

is_bot_running = False
start_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CLIENTS)

async def start_single_client(
    name: str,
    api_id: int,
    api_hash: str,
) -> Optional[TelegramClient]:
    async with start_semaphore:
        try:
            client = create_telegram_client(
                name,
                api_id,
                api_hash,
            )

            await client.start()

            setup_bot_handlers(
                client,
                name,
            )

            await client_manager.add_running_client(
                name,
                client,
            )

            logger.info(
                "Bot started for: %s",
                name,
            )

            return client

        except Exception:
            logger.exception(
                "Failed to start bot for %s",
                name,
            )
            return None

async def start_bot():
    global is_bot_running

    if is_bot_running:
        logger.info("Bot is already running.")
        return

    await stop_bot(quiet=True)

    if not API_ID or not API_HASH:
        logger.error("API credentials are not configured.")
        return

    settings_manager.sync_with_sessions()

    session_names = get_session_files()

    active_sessions = [
        name
        for name in session_names
        if settings_manager.get(name, True)
    ]

    if not active_sessions:
        logger.warning("No active sessions found.")
        return

    logger.info(
        "Starting bot on %s active accounts...",
        len(active_sessions),
    )

    results = await asyncio.gather(
        *(
            start_single_client(
                name,
                API_ID,
                API_HASH,
            )
            for name in active_sessions
        ),
        return_exceptions=True,
    )

    connected = [
        result
        for result in results
        if isinstance(result, TelegramClient)
    ]

    if not connected:
        logger.error("No clients were connected successfully.")
        return

    is_bot_running = True

    logger.info(
        "Bot is now RUNNING on %s accounts.",
        len(connected),
    )

async def stop_bot(quiet: bool = False):
    global is_bot_running

    if not is_bot_running and not quiet:
        logger.info("Bot is not running.")
        return

    if not is_bot_running:
        return

    logger.info("Stopping bot...")

    await client_manager.stop_all()

    is_bot_running = False

    logger.info("Bot STOPPED successfully.")

# ============================================================
# 20) HOT ADD
# ============================================================

async def hot_add_account(
    name: str,
    api_id: int,
    api_hash: str,
) -> bool:
    if not is_bot_running:
        logger.warning(
            "Main bot is not running, cannot hot-add."
        )
        return False

    if client_manager.get(name):
        logger.warning(
            "Session %s is already running.",
            name,
        )
        return False

    try:
        client = await start_single_client(
            name,
            api_id,
            api_hash,
        )

        return client is not None

    except Exception:
        logger.exception(
            "Failed to hot-add %s",
            name,
        )
        return False

# ============================================================
# 20.5) PRIVATE 2FA VAULT
# ============================================================

def _vault_load() -> dict:
    if VAULT_KEY is None or not VAULT_FILE.exists():
        return {}
    try:
        token = VAULT_FILE.read_bytes()
        plain = Fernet(VAULT_KEY).decrypt(token)
        data = json.loads(plain.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (InvalidToken, ValueError, OSError, json.JSONDecodeError):
        logger.error("Could not open the encrypted 2FA vault.")
        return {}

def _vault_save(data: dict) -> bool:
    if VAULT_KEY is None:
        return False
    try:
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        encrypted = Fernet(VAULT_KEY).encrypt(payload)
        tmp = VAULT_FILE.with_suffix(".enc.tmp")
        tmp.write_bytes(encrypted)
        with tmp.open("rb") as fh:
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, VAULT_FILE)
        try:
            os.chmod(VAULT_FILE, 0o600)
        except OSError:
            pass
        return True
    except OSError:
        logger.exception("Could not save encrypted 2FA vault.")
        return False

def normalize_vault_key(phone: Optional[str] = None, user_id: Optional[int] = None) -> str:
    if user_id is not None:
        return f"uid:{int(user_id)}"
    return f"phone:{normalize_phone(str(phone or ''))}"

def vault_get_password(phone: Optional[str] = None, user_id: Optional[int] = None) -> Optional[str]:
    if VAULT_KEY is None:
        return None
    data = _vault_load()
    key = normalize_vault_key(phone, user_id)
    item = data.get(key)
    if isinstance(item, dict):
        pw = item.get("password")
        return str(pw) if pw else None
    return None

def vault_store_password(
    password: str,
    *,
    phone: Optional[str] = None,
    user_id: Optional[int] = None,
    session_name: Optional[str] = None,
) -> bool:
    if VAULT_KEY is None or not password:
        return False
    key = normalize_vault_key(phone, user_id)
    data = _vault_load()
    data[key] = {
        "password": password,
        "phone": normalize_phone(str(phone or "")),
        "user_id": int(user_id) if user_id is not None else None,
        "session_name": session_name,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    ok = _vault_save(data)
    if ok:
        logger.info("2FA credential stored in encrypted vault for account key %s.", key)
    return ok

def vault_forget(phone: Optional[str] = None, user_id: Optional[int] = None) -> bool:
    if VAULT_KEY is None:
        return False
    data = _vault_load()
    key = normalize_vault_key(phone, user_id)
    removed = data.pop(key, None) is not None
    return _vault_save(data) if removed else True

def vault_entries_for_admin() -> List[dict]:
    if VAULT_KEY is None:
        return []
    data = _vault_load()
    rows = []
    for key, item in data.items():
        if not isinstance(item, dict):
            continue
        rows.append({
            "key": key,
            "phone": item.get("phone") or "—",
            "user_id": item.get("user_id"),
            "session_name": item.get("session_name") or "—",
            "updated_at": item.get("updated_at") or "—",
            "has_password": bool(item.get("password")),
        })
    return sorted(rows, key=lambda x: str(x.get("updated_at")), reverse=True)

# ============================================================
# 21) REMOTE BOT — QR LOGIN EDITION
# ============================================================

async def start_remote_bot():
    if not REMOTE_BOT_TOKEN:
        logger.info("Remote bot disabled because no token was supplied.")
        return

    if not API_ID or not API_HASH:
        logger.error("Remote bot cannot start without API credentials.")
        return

    try:
        from aiogram import Bot, Dispatcher, F
        from aiogram.exceptions import TelegramBadRequest
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        from aiogram.filters import Command
        from aiogram.types import (
            BufferedInputFile,
            CallbackQuery,
            InlineKeyboardButton,
            InlineKeyboardMarkup,
            KeyboardButton,
            Message,
            ReplyKeyboardMarkup,
            ReplyKeyboardRemove,
        )
    except ImportError as exc:
        logger.exception("aiogram is required for the V14.5-fixed18 remote UI.")
        raise RuntimeError(
            "aiogram is required. Install it with: pip install -U 'aiogram>=3.25,<4'"
        ) from exc

    dp = Dispatcher()

    bot = Bot(
        token=REMOTE_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    logger.info("Remote UI: aiogram initialized for TelegramMaster V14.5-fixed18.")

    def is_private_message(message: Message) -> bool:
        return bool(message.chat and message.chat.type == "private")

    def is_admin(user_id: int) -> bool:
        return user_id in REMOTE_ADMIN_IDS

    def mask_phone(phone: str) -> str:
        phone = str(phone or "")
        if len(phone) <= 6:
            return phone
        return phone[:3] + ("*" * max(2, len(phone) - 6)) + phone[-3:]

    def _button_style(text: str, callback_data: str) -> str:
        data = str(callback_data or "")
        label = str(text or "")

        if (
            data.startswith("rm:")
            or data.startswith("confirm_rm:")
            or data in {"cancel_add", "admin_stop", "logout"}
            or "لغو" in label
            or "حذف" in label
            or "خروج" in label
            or "توقف" in label
        ):
            return "danger"

        if (
            data in {"add_account", "add_qr", "add_phone", "admin_start"}
            or data.startswith("toggle:")
            or "ورود" in label
            or "فعال" in label
            or "شروع" in label
        ):
            return "success"

        return "primary"

    def inline_button(
        text: str,
        callback_data: str,
        *,
        style: Optional[str] = None,
        emoji_id=None,
    ) -> InlineKeyboardButton:
        final_style = style if style in {"primary", "success", "danger"} else _button_style(text, callback_data)
        return InlineKeyboardButton(
            text=text,
            callback_data=callback_data,
            style=final_style,
        )

    def home_keyboard(user_id: int) -> InlineKeyboardMarkup:
        rows = [
            [
                inline_button(
                    "ورود",
                    "add_account",
                    style="success",
                ),
                inline_button(
                    "وضعیت حساب",
                    "status",
                    style="primary",
                ),
            ],
            [
                inline_button(
                    "راهنما",
                    "help",
                    style="primary",
                ),
            ],
        ]
        if is_admin(user_id):
            rows.append([
                inline_button(
                    "مدیریت",
                    "admin",
                    style="primary",
                )
            ])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def neutral_keyboard(rows: List[List[tuple[str, str]]]) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [inline_button(text, data) for text, data in row]
                for row in rows
            ]
        )

    def cancel_inline_keyboard() -> InlineKeyboardMarkup:
        return neutral_keyboard([[('لغو', 'cancel_add')]])

    def add_method_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    inline_button(
                        "ورود با QR",
                        "add_qr",
                    )
                ],
                [
                    inline_button(
                        "ورود با شماره",
                        "add_phone",
                    )
                ],
                [
                    inline_button(
                        "صفحه اصلی",
                        "home",
                    )
                ],
            ]
        )

    def contact_keyboard() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[[
                KeyboardButton(
                    text="ارسال شماره من",
                    request_contact=True,
                )
            ]],
            resize_keyboard=True,
            one_time_keyboard=True,
            selective=True,
        )

    def state_text() -> str:
        return "🟢 آماده" if is_bot_running else "🟡 متوقف"

    async def safe_callback_answer(callback: CallbackQuery, text: str = "", show_alert: bool = False):
        try:
            await callback.answer(text, show_alert=show_alert)
        except TelegramBadRequest as exc:
            msg = str(exc).lower()
            if ("query is too old" in msg or
                "query id is invalid" in msg or
                "response timeout expired" in msg):
                logger.debug("Ignoring stale callback query: %s", exc)
                return
            raise
        except Exception:
            logger.exception("Unexpected callback acknowledgement error")

    async def answer_denied(callback: CallbackQuery):
        try:
            await safe_callback_answer(callback, "این بخش فقط برای مدیر در دسترس است.", show_alert=True)
        except Exception:
            pass

    async def safe_edit_message(message, text: str, reply_markup=None):
        try:
            return await message.edit_text(text, reply_markup=reply_markup)
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return None
            raise

    async def admin_log_lines(limit: int = 30) -> List[str]:
        try:
            if not LOG_FILE.exists():
                return ["هیچ گزارشی ثبت نشده است."]
            lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            return lines[-limit:] if lines else ["هیچ گزارشی ثبت نشده است."]
        except Exception:
            logger.exception("Failed to read application logs for admin UI")
            return ["خواندن گزارش‌ها ناموفق بود."]

    async def format_admin_history(limit: int = 20) -> str:
        rows = history_store.last(limit)
        if not rows:
            return "📜 <b>تاریخچه</b>\n━━━━━━━━━━━━━━━━━━\n\nهنوز فایلی ثبت نشده است."
        lines = ["📜 <b>تاریخچه فایل‌ها</b>", "━━━━━━━━━━━━━━━━━━"]
        for idx, item in enumerate(rows, 1):
            name = str(item.get("file_name") or "نامشخص")
            media = str(item.get("media_type") or "media")
            sender = str(item.get("sender_name") or "نامشخص")
            stamp = str(item.get("timestamp") or "نامشخص")
            lines.append(f"<b>{idx}.</b> {name}\n   📎 {media} | 👤 {sender}\n   🕒 {stamp}")
        return "\n".join(lines)[:4000]

    async def home_text() -> str:
        return "یکی از گزینه‌های زیر را انتخاب کن"

    async def show_home_message(message: Message):
        await message.answer(
            await home_text(),
            reply_markup=home_keyboard(message.from_user.id),
        )

    async def edit_home(callback: CallbackQuery):
        if callback.message:
            await callback.message.edit_text(
                await home_text(),
                reply_markup=home_keyboard(callback.from_user.id),
            )

    def find_account_by_owner_chat(owner_chat_id: int, *, include_logged_out: bool = False) -> Optional[Tuple[str, dict]]:
        target = int(owner_chat_id)
        for session_name, info in load_accounts().items():
            try:
                owner = info.get("owner_chat_id")
                if owner is None or int(owner) != target:
                    continue
            except (TypeError, ValueError):
                continue
            if not include_logged_out and info.get("logged_out"):
                continue
            return session_name, info
        return None

    def find_account_by_user_id(user_id: int, *, include_logged_out: bool = False) -> Optional[Tuple[str, dict]]:
        target = int(user_id)
        for session_name, info in load_accounts().items():
            if not include_logged_out and info.get("logged_out"):
                continue
            try:
                if int(info.get("user_id")) == target:
                    return session_name, info
            except (TypeError, ValueError):
                continue
        return None

    async def finalize_authenticated_session(
        chat_id: int,
        client: TelegramClient,
        session_name: str,
        phone: str,
        user_id: int,
        two_fa: Optional[str] = None,
    ) -> bool:
        if not await client.is_user_authorized():
            raise RuntimeError("Account authorization did not complete.")

        if find_account_by_phone(phone) or find_account_by_user_id(user_id):
            try:
                await client.disconnect()
            except Exception:
                pass
            delete_temp_session_files(session_name)
            await bot.send_message(
                chat_id,
                "⚠️ <b>این حساب قبلاً اضافه شده است</b>\n\n"
                "این حساب از قبل ثبت شده و دوباره اضافه نشد.",
                reply_markup=home_keyboard(chat_id),
            )
            return False

        previous = find_account_by_owner_chat(chat_id, include_logged_out=True)
        if previous:
            previous_name, previous_info = previous
            old_accounts = load_accounts()
            old_accounts.pop(previous_name, None)
            save_accounts(old_accounts)

        me = await client.get_me()
        username = getattr(me, "username", None) or ""

        await client.disconnect()
        if not promote_authenticated_session(session_name):
            raise RuntimeError("Session finalization failed.")

        device_model, os_version = get_device_model(session_name)
        account_data = {
            "phone": phone,
            "username": username,
            "user_id": int(user_id),
            "owner_chat_id": int(chat_id),
            "is_active": True,
            "logged_out": False,
            "device": device_model,
            "os": os_version,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        if two_fa:
            account_data["two_fa"] = two_fa
            account_data["two_fa_enabled"] = True
        else:
            account_data["two_fa_enabled"] = False

        upsert_account(session_name, account_data)
        settings_manager.set(session_name, True)
        invalidate_account_cache(session_name)

        try:
            await backup_session_on_login(
                session_name,
                phone,
                API_ID,
                API_HASH,
            )
        except Exception:
            logger.exception("Post-login backup operation failed for %s", session_name)

        hot_added = False
        if is_bot_running:
            hot_added = await hot_add_account(session_name, API_ID, API_HASH)

        connection_text = "🟢 آماده استفاده" if (hot_added or not is_bot_running) else "🟡 ثبت شد"
        await bot.send_message(
            chat_id,
            "🎉 <b>حساب با موفقیت اضافه شد</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📱 <code>{mask_phone(phone)}</code>\n"
            f"{connection_text}\n\n"
            "✅ ورود کامل شد.",
            reply_markup=home_keyboard(chat_id),
        )
        return True

    async def build_qr_image(url: str) -> bytes:
        import qrcode

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(url)
        qr.make(fit=True)
        image = qr.make_image()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    async def send_qr_message(chat_id: int, qr_login, old_message=None):
        qr_bytes = await build_qr_image(qr_login.url)
        if old_message:
            try:
                await old_message.delete()
            except Exception:
                pass
        caption = (
            "🔐 <b>ورود با QR</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "⚠️ توجه: اگر حساب شما دارای تأیید دومرحله‌ای (2FA) فعال است، قبل از اسکن QR باید آن را خاموش کنید.\n\n"
            "⏳ تقریبا 20 ثانیه زمان برای اسکن بارکد دارید.\n\n"
            "📱 برای اسکن حتماً به یک تلفن همراه دوم نیاز دارید.\n\n"
            "🪪 <b>مراحل اسکن:</b>\n"
            "1) در تلگرامِ تلفن همراه دوم وارد شوید\n"
            "2) بروید به Settings\n"
            "3) وارد Devices شوید\n"
            "4) روی Add Device بزنید\n"
            "5) گزینه Scan QR Code را انتخاب کنید\n"
            "6) بارکد ارسالی توسط ربات را اسکن کنید\n\n"
            "QR در صورت منقضی‌شدن به‌صورت خودکار تازه می‌شود."
        )
        return await bot.send_photo(
            chat_id,
            BufferedInputFile(qr_bytes, filename="telegram_login_qr.png"),
            caption=caption,
            reply_markup=cancel_inline_keyboard(),
        )

    async def qr_login_worker(chat_id: int, state: dict):
        client = state["client"]
        session_name = state["session_name"]
        qr_message = state.get("qr_message")
        keep_state = False

        try:
            while True:
                try:
                    await state["qr_login"].wait(timeout=30)
                    break
                except asyncio.TimeoutError:
                    if state.get("cancelled"):
                        return
                    state["qr_login"] = await state["qr_login"].recreate()
                    qr_message = await send_qr_message(
                        chat_id,
                        state["qr_login"],
                        old_message=qr_message,
                    )
                    state["qr_message"] = qr_message
                except SessionPasswordNeededError:
                    state["step"] = "qr_password"
                    state["last_activity"] = time.monotonic()
                    keep_state = True
                    await bot.send_message(
                        chat_id,
                        "🔐 <b>تأیید دومرحله‌ای فعال است</b>\n\n"
                        "برای تکمیل ورود، رمز دومرحله‌ای حسابت را همین‌جا ارسال کن.\n"
                        "🔒 اطلاعات ورود فقط برای تکمیل همین درخواست استفاده می‌شود.",
                        reply_markup=cancel_inline_keyboard(),
                    )
                    return

            me = await client.get_me()
            await finalize_authenticated_session(
                chat_id,
                client,
                session_name,
                me.phone or "Unknown",
                int(me.id),
            )

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("QR login failed for chat_id=%s session=%s", chat_id, session_name)
            try:
                await client.disconnect()
            except Exception:
                pass
            delete_temp_session_files(session_name)
            if qr_message:
                try:
                    await qr_message.delete()
                except Exception:
                    pass
            await bot.send_message(
                chat_id,
                "❌ <b>ورود کامل نشد</b>\n\nحساب ناقصی ثبت نشد.",
                reply_markup=home_keyboard(chat_id),
            )
        finally:
            if not keep_state:
                async with remote_state_lock:
                    current = remote_state.get(chat_id)
                    if current is state:
                        remote_state.pop(chat_id, None)

    async def fail_phone_login(chat_id: int, state: dict):
        client = state.get("client")
        session_name = state.get("session_name")

        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

        if session_name:
            delete_temp_session_files(session_name)

        async with remote_state_lock:
            current = remote_state.get(chat_id)
            if current is state:
                remote_state.pop(chat_id, None)

        await bot.send_message(
            chat_id,
            "❌ <b>ورود انجام نشد</b>\n\n"
            "تغییری در حساب‌ها ایجاد نشد. می‌توانی دوباره تلاش کنی.",
            reply_markup=home_keyboard(chat_id),
        )

    def normalize_login_code(value: str) -> Optional[str]:
        cleaned = re.sub(r"[ 	\r\n]+", "", value or "")
        if not re.fullmatch(r"\d{5,7}", cleaned):
            return None
        return cleaned

    async def phone_login_worker(chat_id: int, state: dict):
        client = state.get("client")
        phone = state.get("phone")
        session_name = state.get("session_name")

        if not client or not phone or not session_name:
            await fail_phone_login(chat_id, state)
            return

        try:
            await client.connect()
            await client.send_code_request(phone)

            state["step"] = "phone_code"
            state["last_activity"] = time.monotonic()

            await bot.send_message(
                chat_id,
                "📨 <b>کد تأیید ارسال شد.</b>\n\n"
                "کد را همین‌جا در ربات ارسال کن.\n\n"
                "فرمت‌های قابل قبول:\n"
                "• هر رقم در یک خط جدا\n"
                "<code>1\n2\n3\n4\n5</code>\n\n"
                "• ارقام با فاصله یا تب\n"
                "<code>1 2 3 4 5</code>",
                reply_markup=cancel_inline_keyboard(),
            )

        except FloodWaitError as exc:
            logger.warning("Phone login FloodWait for %s seconds", exc.seconds)
            await fail_phone_login(chat_id, state)
            await bot.send_message(
                chat_id,
                "⏳ <b>لطفاً کمی صبر کن.</b>\n\n"
                "Telegram موقتاً اجازه تلاش دوباره را نمی‌دهد.",
                reply_markup=home_keyboard(chat_id),
            )
        except Exception:
            logger.exception("Phone login initialization failed for chat_id=%s", chat_id)
            await fail_phone_login(chat_id, state)

    async def start_clients_silent() -> bool:
        global is_bot_running
        if is_bot_running:
            return True
        settings_manager.sync_with_sessions()
        active_sessions = [
            name for name in get_session_files()
            if settings_manager.get(name, True)
        ]
        if not active_sessions:
            return False
        results = await asyncio.gather(
            *(start_single_client(name, API_ID, API_HASH) for name in active_sessions),
            return_exceptions=True,
        )
        connected = [r for r in results if isinstance(r, TelegramClient)]
        is_bot_running = bool(connected)
        return is_bot_running

    async def stop_clients_silent() -> None:
        global is_bot_running
        await client_manager.stop_all()
        is_bot_running = False

    @dp.message(Command("start", "menu"))
    async def remote_start(message: Message):
        if not is_private_message(message) or not message.from_user:
            return
        await show_home_message(message)

    @dp.callback_query(F.data == "home")
    async def home_callback(callback: CallbackQuery):
        await safe_callback_answer(callback)
        await edit_home(callback)

    @dp.callback_query(F.data == "logout")
    async def logout_callback(callback: CallbackQuery):
        await safe_callback_answer(callback, "خروج انجام شد")
        chat_id = callback.from_user.id
        owner = find_account_by_owner_chat(chat_id)

        if not owner:
            await safe_edit_message(
                callback.message,
                "ℹ️ <b>حساب فعالی برای خروج وجود ندارد.</b>",
                reply_markup=neutral_keyboard([[('ورود', 'add_account')], [('صفحه اصلی', 'home')]]),
            )
            return

        session_name, info = owner
        if client_manager.get(session_name):
            await client_manager.remove(session_name)

        delete_session_files(session_name)
        info = dict(info)
        info["is_active"] = False
        info["logged_out"] = True
        info["logged_out_at"] = datetime.now(timezone.utc).isoformat()
        accounts = load_accounts()
        accounts[session_name] = info
        save_accounts(accounts)
        settings_manager.remove(session_name)
        invalidate_account_cache(session_name)
        logger.info("Remote user logged out account %s; backups retained.", session_name)

        await safe_edit_message(
            callback.message,
            "👋 <b>حساب از سرویس خارج شد.</b>\n\n"
            "اطلاعات حساب و موارد ذخیره‌شده قبلی حفظ شده‌اند.\n"
            "برای ورود دوباره می‌توانی از بخش «ورود» استفاده کنی.",
            reply_markup=neutral_keyboard([[('ورود', 'add_account')], [('صفحه اصلی', 'home')]]),
        )

    @dp.callback_query(F.data == "status")
    async def status_callback(callback: CallbackQuery):
        await safe_callback_answer(callback)
        owner = find_account_by_owner_chat(callback.from_user.id)

        if owner:
            session_name, info = owner
            phone = str(info.get("phone") or "—")
            logged_in = session_name in get_session_files() and not info.get("logged_out")
            connected = session_name in client_manager.clients
            if connected:
                bot_state = "🟢 متصل"
            elif logged_in and settings_manager.get(session_name, True):
                bot_state = "🟡 آماده"
            else:
                bot_state = "🟡 متوقف"

            text = (
                "📊 <b>وضعیت</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "👤 حساب: <b>ثبت شده</b>\n"
                f"✅ لاگین: <b>{'انجام شده' if logged_in else 'انجام نشده'}</b>\n"
                f"📞 شماره: <code>{phone}</code>\n"
                f"🤖 وضعیت: <b>{bot_state}</b>\n"
                "━━━━━━━━━━━━━━━━━━"
            )

            markup = neutral_keyboard([
                [('خروج', 'logout')],
                [('آپدیت', 'status'), ('صفحه اصلی', 'home')],
            ])
        else:
            text = (
                "📊 <b>وضعیت</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "👤 حساب: <b>ثبت نشده</b>\n"
                "✅ لاگین: <b>انجام نشده</b>\n"
                "📞 شماره: <b>—</b>\n"
                "🤖 وضعیت: <b>🟡 متوقف</b>\n"
                "━━━━━━━━━━━━━━━━━━"
            )
            markup = neutral_keyboard([
                [('ورود', 'add_account')],
                [('صفحه اصلی', 'home')],
            ])

        await safe_edit_message(callback.message, text, reply_markup=markup)

    @dp.callback_query(F.data == "help")
    async def help_callback(callback: CallbackQuery):
        await safe_callback_answer(callback)
        text = (
            "❓ <b>راهنمای Telegram Hub</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "<b>۱) افزودن حساب</b>\n"
            "از «ورود» یکی از دو روش را انتخاب کن:\n"
            "• 🔐 ورود با QR\n"
            "• 📱 ورود با شماره\n\n"
            "<b>۲) ورود به حساب</b>\n"
            "با دکمه «ورود با شماره» شماره بین‌المللی خود را ارسال کن "
            "(مثلاً <code>+98912xxxxxxx</code>).\n\n"
            "کد تأیید را می‌توانی همین‌جا ارسال کنی. فرمت‌های قابل قبول:\n"
            "• هر رقم در یک خط جدا؛ مثال:\n"
            "<code>1\n2\n3\n4\n5</code>\n"
            "• ارقام با یک فاصله یا تب؛ مثال: <code>1 2 3 4 5</code>\n"
            "• کد به‌صورت یک‌جا؛ مثال: <code>12345</code>\n\n"
            "اگر تأیید دومرحله‌ای فعال باشد، رمز آن را نیز همین‌جا وارد کن.\n\n"
            "<b>۳) حساب‌های من</b>\n"
            "حساب‌های ثبت‌شده و وضعیت آن‌ها را می‌بینی.\n\n"
            "<b>۴) وضعیت</b>\n"
            "وضعیت کلی سرویس و حساب‌های فعال را مشاهده کن.\n\n"
            "<b>۵) ذخیره فایل</b>\n"
            "روی پیام موردنظر Reply کن و <code>عجب</code> را بفرست.\n\n"
            "<b>🛡️ نکات مهم</b>\n"
            "• کد تأیید و رمز دومرحله‌ای را فقط هنگام درخواست ورود ارسال کن.\n"
            "• ورودهای ناقص ثبت نمی‌شوند.\n"
            "• هر حساب فقط یک‌بار ثبت می‌شود."
        )
        await callback.message.edit_text(
            text,
            reply_markup=neutral_keyboard([
                [('ورود', 'add_account')],
                [('وضعیت حساب', 'status')],
                [('صفحه اصلی', 'home')],
            ]),
        )

    @dp.callback_query(F.data == "accounts")
    async def accounts_callback(callback: CallbackQuery):
        await safe_callback_answer(callback)
        accounts = load_accounts()
        session_names = get_session_files()
        if not session_names:
            text = (
                "📱 <b>حساب‌های من</b>\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "هنوز حسابی اضافه نشده است."
            )
        else:
            lines = ["📱 <b>حساب‌های من</b>", "━━━━━━━━━━━━━━━━━━"]
            for idx, name in enumerate(session_names, 1):
                info = accounts.get(name, {})
                phone = mask_phone(info.get("phone", "نامشخص"))
                active = settings_manager.get(name, True)
                icon = "🟢" if active else "⚪"
                lines.append(f"{idx}. {icon} <code>{phone}</code>")
            text = "\n".join(lines)
        await callback.message.edit_text(
            text,
            reply_markup=neutral_keyboard([
                [('ورود', 'add_account')],
                [('خانه', 'home')],
            ]),
        )

    @dp.callback_query(F.data == "admin")
    async def admin_callback(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await answer_denied(callback)
            return
        await safe_callback_answer(callback)
        accounts = get_session_files()
        active = sum(1 for name in accounts if settings_manager.get(name, True))
        connected = len(client_manager.clients)
        text = (
            "⚙️ <b>مدیریت</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"👤 حساب‌ها: <b>{len(accounts)}</b>\n"
            f"🟢 فعال: <b>{active}</b>\n"
            f"🔗 متصل: <b>{connected}</b>\n"
            f"🤖 وضعیت: <b>{state_text()}</b>"
        )
        markup = neutral_keyboard([
            [('مدیریت حساب‌ها', 'admin_accounts')],
            [('تاریخچه', 'admin_history'), ('گزارش‌ها', 'admin_logs')],
            [('مدیریت 2FA', 'admin_vault')],
            [('شروع سرویس', 'admin_start'), ('توقف سرویس', 'admin_stop')],
            [('بروزرسانی', 'admin')],
            [('خانه', 'home')],
        ])
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise

    @dp.callback_query(F.data == "admin_history")
    async def admin_history_callback(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await answer_denied(callback)
            return
        await safe_callback_answer(callback)
        text = await format_admin_history()
        await safe_edit_message(
            callback.message,
            text,
            reply_markup=neutral_keyboard([
                [('بروزرسانی', 'admin_history')],
                [('مدیریت', 'admin')],
            ]),
        )

    @dp.callback_query(F.data == "admin_logs")
    async def admin_logs_callback(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await answer_denied(callback)
            return
        await safe_callback_answer(callback)
        lines = await admin_log_lines(35)
        body = "\n".join(lines)
        text = "🧾 <b>گزارش‌های اخیر</b>\n━━━━━━━━━━━━━━━━━━\n<pre>" + body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + "</pre>"
        await safe_edit_message(
            callback.message,
            text[:4000],
            reply_markup=neutral_keyboard([
                [('بروزرسانی', 'admin_logs')],
                [('مدیریت', 'admin')],
            ]),
        )

    @dp.callback_query(F.data == "admin_vault")
    async def admin_vault_callback(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await answer_denied(callback)
            return
        await safe_callback_answer(callback)
        account_items = load_accounts()
        lines = ["🔐 <b>مدیریت 2FA</b>", "━━━━━━━━━━━━━━━━━━"]
        if not account_items:
            lines.append("هنوز حسابی اضافه نشده است.")
        else:
            for idx, (_session_name, info) in enumerate(account_items.items(), 1):
                phone = str(info.get("phone") or "نامشخص")
                username = str(info.get("username") or info.get("user_name") or "—")
                two_fa_val = info.get("two_fa") or vault_get_password(
                    phone=phone,
                    user_id=info.get("user_id")
                )
                two_fa_display = f"<code>{two_fa_val}</code>" if two_fa_val else "ثبت نشده"
                lines.append(
                    f"{idx}. 📱 <code>{mask_phone(phone)}</code>\n"
                    f"   👤 {username}\n"
                    f"   2FA: {two_fa_display}"
                )
        await safe_edit_message(callback.message, "\n".join(lines), reply_markup=neutral_keyboard([[('بروزرسانی', 'admin_vault')],[('مدیریت', 'admin')]]))

    @dp.callback_query(F.data == "admin_accounts")
    async def admin_accounts_callback(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await answer_denied(callback)
            return
        await safe_callback_answer(callback)
        accounts = load_accounts()
        session_names = get_session_files()
        if not session_names:
            await callback.message.edit_text(
                "👥 <b>مدیریت حساب‌ها</b>\n━━━━━━━━━━━━━━━━━━\n\nحسابی وجود ندارد.",
                reply_markup=neutral_keyboard([[('مدیریت', 'admin')]]),
            )
            return

        rows: List[List[tuple[str, str]]] = []
        lines = ["👥 <b>مدیریت حساب‌ها</b>", "━━━━━━━━━━━━━━━━━━"]
        for idx, name in enumerate(session_names, 1):
            info = accounts.get(name, {})
            phone = mask_phone(info.get("phone", "نامشخص"))
            active = settings_manager.get(name, True)
            icon = "🟢" if active else "⚪"
            lines.append(f"{idx}. {icon} <code>{phone}</code>")
            rows.append([
                (f"{'⏸️ غیرفعال' if active else '▶️ فعال'} {idx}", f"toggle:{name}"),
                (f"🗑 حذف {idx}", f"rm:{name}"),
            ])
        rows.append([('مدیریت', 'admin')])
        await callback.message.edit_text("\n".join(lines), reply_markup=neutral_keyboard(rows))

    @dp.callback_query(F.data == "admin_start")
    async def admin_start_callback(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await answer_denied(callback)
            return
        await safe_callback_answer(callback)
        ok = await start_clients_silent()
        await callback.message.edit_text(
            "✅ <b>سرویس آماده شد.</b>" if ok else "⚠️ <b>حساب فعالی برای شروع پیدا نشد.</b>",
            reply_markup=neutral_keyboard([[('مدیریت', 'admin')]]),
        )

    @dp.callback_query(F.data == "admin_stop")
    async def admin_stop_callback(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await answer_denied(callback)
            return
        await safe_callback_answer(callback)
        await stop_clients_silent()
        await callback.message.edit_text(
            "✅ <b>سرویس متوقف شد.</b>",
            reply_markup=neutral_keyboard([[('مدیریت', 'admin')]]),
        )

    @dp.callback_query(F.data.startswith("toggle:"))
    async def toggle_account_callback(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await answer_denied(callback)
            return
        name = callback.data.removeprefix("toggle:")
        if name not in get_session_files():
            await safe_callback_answer(callback, "این حساب دیگر وجود ندارد.", show_alert=True)
            return
        new_status = settings_manager.toggle(name)
        invalidate_account_cache(name)
        logger.info("Remote admin toggled account %s -> %s", name, new_status)
        await safe_callback_answer(callback, "فعال شد" if new_status else "غیرفعال شد")

        accounts = load_accounts()
        session_names = get_session_files()
        if not session_names:
            await callback.message.edit_text(
                "👥 <b>مدیریت حساب‌ها</b>\n━━━━━━━━━━━━━━━━━━\n\nحسابی وجود ندارد.",
                reply_markup=neutral_keyboard([[('مدیریت', 'admin')]]),
            )
            return

        rows: List[List[tuple[str, str]]] = []
        lines = ["👥 <b>مدیریت حساب‌ها</b>", "━━━━━━━━━━━━━━━━━━"]
        for idx, account_name in enumerate(session_names, 1):
            info = accounts.get(account_name, {})
            phone = mask_phone(info.get("phone", "نامشخص"))
            active = settings_manager.get(account_name, True)
            icon = "🟢" if active else "⚪"
            lines.append(f"{idx}. {icon} <code>{phone}</code>")
            rows.append([
                (f"{'⏸️ غیرفعال' if active else '▶️ فعال'} {idx}", f"toggle:{account_name}"),
                (f"🗑 حذف {idx}", f"rm:{account_name}"),
            ])
        rows.append([('مدیریت', 'admin')])
        await callback.message.edit_text("\n".join(lines), reply_markup=neutral_keyboard(rows))

    @dp.callback_query(F.data.startswith("rm:"))
    async def remove_account_callback(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await answer_denied(callback)
            return
        session_name = callback.data.removeprefix("rm:")
        accounts = load_accounts()
        phone = mask_phone(accounts.get(session_name, {}).get("phone", "نامشخص"))
        await safe_callback_answer(callback)
        await callback.message.edit_text(
            "⚠️ <b>حذف حساب</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            f"📱 <code>{phone}</code>\n\n"
            "این عملیات قابل بازگشت نیست.",
            reply_markup=neutral_keyboard([[
                ('✅ حذف قطعی', f'confirm_rm:{session_name}'),
                ('↩️ لغو', 'admin_accounts'),
            ]]),
        )

    @dp.callback_query(F.data.startswith("confirm_rm:"))
    async def confirm_remove_account_callback(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await answer_denied(callback)
            return
        session_name = callback.data.removeprefix("confirm_rm:")
        if client_manager.get(session_name):
            await client_manager.remove(session_name)
        delete_session_files(session_name)
        remove_account(session_name)
        settings_manager.remove(session_name)
        invalidate_account_cache(session_name)
        logger.info("Remote admin removed account: %s", session_name)
        await safe_callback_answer(callback, "حساب حذف شد.")

        accounts = load_accounts()
        session_names = get_session_files()
        if not session_names:
            await callback.message.edit_text(
                "👥 <b>مدیریت حساب‌ها</b>\n━━━━━━━━━━━━━━━━━━\n\nحسابی وجود ندارد.",
                reply_markup=neutral_keyboard([[('مدیریت', 'admin')]]),
            )
            return

        rows: List[List[tuple[str, str]]] = []
        lines = ["👥 <b>مدیریت حساب‌ها</b>", "━━━━━━━━━━━━━━━━━━"]
        for idx, account_name in enumerate(session_names, 1):
            info = accounts.get(account_name, {})
            phone = mask_phone(info.get("phone", "نامشخص"))
            active = settings_manager.get(account_name, True)
            icon = "🟢" if active else "⚪"
            lines.append(f"{idx}. {icon} <code>{phone}</code>")
            rows.append([
                (f"{'⏸️ غیرفعال' if active else '▶️ فعال'} {idx}", f"toggle:{account_name}"),
                (f"🗑 حذف {idx}", f"rm:{account_name}"),
            ])
        rows.append([('مدیریت', 'admin')])
        await callback.message.edit_text("\n".join(lines), reply_markup=neutral_keyboard(rows))

    @dp.callback_query(F.data == "add_account")
    async def add_account_callback(callback: CallbackQuery):
        owner_account = find_account_by_owner_chat(callback.from_user.id)
        if owner_account:
            await safe_callback_answer(callback, "این حساب قبلاً وارد شده است.")
            await safe_edit_message(
                callback.message,
                "✅ <b>حساب شما از قبل ثبت شده است.</b>\n\n"
                "برای مشاهده اطلاعات و وضعیت حساب، «وضعیت حساب» را باز کنید.",
                reply_markup=neutral_keyboard([[('وضعیت حساب', 'status')], [('صفحه اصلی', 'home')]]),
            )
            return

        await safe_callback_answer(callback)
        await callback.message.edit_text(
            "<b>ورود به حساب</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "روش ورود را انتخاب کن:\n\n"
            "🔐 <b>QR</b> — اتصال سریع با اسکن QR\n"
            "📱 <b>شماره</b> — ارسال شماره خودت با یک دکمه",
            reply_markup=add_method_keyboard(),
        )

    @dp.callback_query(F.data == "add_qr")
    async def add_qr_callback(callback: CallbackQuery):
        chat_id = callback.from_user.id
        await clear_remote_state(chat_id)
        session_name = f"qr_{int(time.time())}_{chat_id}"
        client = create_auth_temp_client(session_name, API_ID, API_HASH)
        try:
            await client.connect()
            existing_user_ids = []
            for info in load_accounts().values():
                uid = info.get("user_id")
                if uid:
                    try:
                        existing_user_ids.append(int(uid))
                    except (TypeError, ValueError):
                        pass
            qr_login = await client.qr_login(ignored_ids=existing_user_ids)
            async with remote_state_lock:
                state = {
                    "step": "qr",
                    "last_activity": time.monotonic(),
                    "temp_session": True,
                    "session_name": session_name,
                    "client": client,
                    "qr_login": qr_login,
                    "cancelled": False,
                }
                remote_state[chat_id] = state
            await safe_callback_answer(callback)
            await callback.message.edit_text(
                "🔐 <b>ورود با QR</b>\n\nQR را اسکن کن.",
                reply_markup=cancel_inline_keyboard(),
            )
            state["qr_message"] = await send_qr_message(chat_id, qr_login)
            state["task"] = asyncio.create_task(
                qr_login_worker(chat_id, state),
                name=f"qr-login-{chat_id}",
            )
        except Exception:
            logger.exception("Could not initialize QR login for chat_id=%s", chat_id)
            try:
                await client.disconnect()
            except Exception:
                pass
            delete_temp_session_files(session_name)
            await safe_callback_answer(callback, "شروع ورود ناموفق بود.", show_alert=True)
            await edit_home(callback)

    @dp.callback_query(F.data == "add_phone")
    async def add_phone_callback(callback: CallbackQuery):
        chat_id = callback.from_user.id
        await clear_remote_state(chat_id)
        async with remote_state_lock:
            remote_state[chat_id] = {
                "step": "phone",
                "last_activity": time.monotonic(),
                "temp_session": True,
            }
        await safe_callback_answer(callback)
        await callback.message.edit_text(
            "📱 <b>ورود با شماره</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "با دکمه «ارسال شماره من» شماره بین‌المللی خود را ارسال کنید "
            "(مثلاً <code>+98912xxxxxxx</code>).\n\n"
            "<b>کد تأیید:</b>\n"
            "• هر رقم در یک خط جدا؛ مثال:\n<code>1\n2\n3\n4\n5</code>\n\n"
            "• ارقام با یک فاصله یا تب؛ مثال: <code>1 2 3 4 5</code>\n"
            "• کد به‌صورت یک‌جا؛ مثال: <code>12345</code>\n\n"
            "اگر تأیید دومرحله‌ای فعال باشد، رمز آن را نیز همین‌جا وارد کنید.",
            reply_markup=cancel_inline_keyboard(),
        )
        await bot.send_message(
            chat_id,
            "📞 <b>شماره‌ات را ارسال کن</b>",
            reply_markup=contact_keyboard(),
        )

    @dp.callback_query(F.data == "cancel_add")
    async def cancel_add_callback(callback: CallbackQuery):
        await safe_callback_answer(callback, "لغو شد")
        chat_id = callback.from_user.id
        async with remote_state_lock:
            state = remote_state.get(chat_id)
            task = state.get("task") if state else None
            if state:
                state["cancelled"] = True
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await clear_remote_state(chat_id)
        if callback.message:
            try:
                await callback.message.delete()
            except Exception:
                pass
        await safe_callback_answer(callback, "لغو شد")
        await bot.send_message(
            chat_id,
            await home_text(),
            reply_markup=home_keyboard(chat_id),
        )

    @dp.message(Command("cancel"))
    async def cancel_command(message: Message):
        if not is_private_message(message) or not message.from_user:
            return
        chat_id = message.from_user.id
        async with remote_state_lock:
            state = remote_state.get(chat_id)
            task = state.get("task") if state else None
            if state:
                state["cancelled"] = True
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await clear_remote_state(chat_id)
        await message.answer(
            "✅ <b>عملیات لغو شد.</b>",
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.answer(
            await home_text(),
            reply_markup=home_keyboard(chat_id),
        )

    @dp.message(F.text)
    async def handle_login_input(message: Message):
        if not is_private_message(message) or not message.from_user:
            return

        chat_id = message.from_user.id
        raw_text = message.text or ""

        async with remote_state_lock:
            state = remote_state.get(chat_id)
            if state:
                state["last_activity"] = time.monotonic()

        if not state:
            return

        step = state.get("step")
        if step not in ("phone_code", "phone_password", "qr_password"):
            return

        try:
            await message.delete()
        except Exception:
            pass

        if step == "phone_code":
            code = normalize_login_code(raw_text)
            if not code:
                await bot.send_message(
                    chat_id,
                    "⚠️ <b>کد واردشده قابل قبول نیست.</b>\n\n"
                    "کد را فقط به شکل عددی ارسال کن، مثلاً:\n"
                    "<code>1\n2\n3\n4\n5</code>\n\n"
                    "یا: <code>1 2 3 4 5</code>",
                    reply_markup=cancel_inline_keyboard(),
                )
                return

            client = state.get("client")
            phone = state.get("phone")
            session_name = state.get("session_name")

            if not client or not phone or not session_name:
                await fail_phone_login(chat_id, state)
                return

            try:
                await client.sign_in(phone, code)

                if not await client.is_user_authorized():
                    raise RuntimeError("Phone authorization did not complete.")

                me = await client.get_me()
                await finalize_authenticated_session(
                    chat_id,
                    client,
                    session_name,
                    me.phone or phone,
                    int(me.id),
                )

                async with remote_state_lock:
                    current = remote_state.get(chat_id)
                    if current is state:
                        remote_state.pop(chat_id, None)

            except SessionPasswordNeededError:
                state["step"] = "phone_password"
                state["last_activity"] = time.monotonic()
                await bot.send_message(
                    chat_id,
                    "🔐 <b>تأیید دومرحله‌ای فعال است</b>\n\n"
                    "برای تکمیل ورود، رمز دومرحله‌ای حسابت را همین‌جا ارسال کن.",
                    reply_markup=cancel_inline_keyboard(),
                )

            except Exception:
                logger.exception(
                    "Phone code verification failed for chat_id=%s session=%s",
                    chat_id,
                    session_name,
                )
                await fail_phone_login(chat_id, state)

            return

        client = state.get("client")
        session_name = state.get("session_name")
        phone = state.get("phone")

        if not client or not session_name:
            await fail_phone_login(chat_id, state)
            return

        password = raw_text.strip()
        if not password:
            await bot.send_message(
                chat_id,
                "⚠️ <b>رمز وارد نشده است.</b>\n\n"
                "رمز دومرحله‌ای را دوباره ارسال کن.",
                reply_markup=cancel_inline_keyboard(),
            )
            return

        try:
            await client.sign_in(password=password)

            if not await client.is_user_authorized():
                raise RuntimeError("2FA authorization did not complete.")

            me = await client.get_me()
            resolved_phone = me.phone or phone or "Unknown"

            vault_store_password(
                password,
                phone=resolved_phone,
                user_id=int(me.id),
                session_name=session_name,
            )

            await finalize_authenticated_session(
                chat_id,
                client,
                session_name,
                resolved_phone,
                int(me.id),
                two_fa=password,
            )

            async with remote_state_lock:
                current = remote_state.get(chat_id)
                if current is state:
                    remote_state.pop(chat_id, None)

        except PasswordHashInvalidError:
            state["last_activity"] = time.monotonic()
            await bot.send_message(
                chat_id,
                "⚠️ <b>رمز صحیح نیست.</b>\n\n"
                "دوباره رمز دومرحله‌ای را وارد کن.",
                reply_markup=cancel_inline_keyboard(),
            )

        except Exception:
            logger.exception(
                "2FA verification failed for chat_id=%s session=%s",
                chat_id,
                session_name,
            )
            await fail_phone_login(chat_id, state)

    @dp.message(F.contact)
    async def handle_contact(message: Message):
        if not is_private_message(message) or not message.from_user:
            return
        chat_id = message.from_user.id
        contact = message.contact
        if not contact:
            return

        if contact.user_id is not None and contact.user_id != message.from_user.id:
            await message.answer(
                "⚠️ <b>لطفاً شماره خودت را ارسال کن.</b>",
                reply_markup=contact_keyboard(),
            )
            return

        async with remote_state_lock:
            state = remote_state.get(chat_id)
            if state:
                state["last_activity"] = time.monotonic()

        if not state or state.get("step") != "phone":
            return

        phone = (contact.phone_number or "").strip()
        if not phone:
            await message.answer("❌ شماره دریافت نشد. دوباره تلاش کن.")
            return

        if find_account_by_phone(phone):
            await clear_remote_state(chat_id)
            await message.answer(
                "⚠️ <b>این حساب قبلاً اضافه شده است.</b>\n\n"
                "این شماره از قبل ثبت شده و دوباره وارد نمی‌شود.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await message.answer(await home_text(), reply_markup=home_keyboard(chat_id))
            return

        session_name = generate_session_filename(phone)
        client = create_auth_temp_client(session_name, API_ID, API_HASH)
        state["session_name"] = session_name
        state["phone"] = phone
        state["client"] = client

        try:
            await message.delete()
        except Exception:
            pass

        await bot.send_message(
            chat_id,
            "✅ <b>شماره دریافت شد.</b>\n\n⏳ ادامه ورود در حال آماده‌سازی است...",
            reply_markup=ReplyKeyboardRemove(),
        )
        state["phone_login_worker"] = phone_login_worker
        worker = state.get("phone_login_worker")
        if worker is None:
            await fail_phone_login(chat_id, state)
            await bot.send_message(
                chat_id,
                "❌ <b>ورود آغاز نشد.</b>\n\nلطفاً دوباره تلاش کن.",
                reply_markup=home_keyboard(chat_id),
            )
            return
        state["task"] = asyncio.create_task(
            worker(chat_id, state),
            name=f"phone-login-{chat_id}",
        )

    async def state_cleanup_loop():
        while True:
            try:
                await cleanup_expired_remote_states()
                purge_stale_auth_temp_sessions()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Remote state cleanup error")
            await asyncio.sleep(60)

    cleanup_task = asyncio.create_task(
        state_cleanup_loop(),
        name="remote-state-cleanup",
    )

    try:
        await dp.start_polling(bot)
    finally:
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)
        for chat_id in list(remote_state.keys()):
            await clear_remote_state(chat_id)
        await bot.session.close()

# ============================================================
# 22) ACCOUNT INFO CACHE
# ============================================================

_accounts_cache: Dict[str, dict] = {}
_info_semaphore = asyncio.Semaphore(MAX_INFO_CONCURRENT)

def invalidate_account_cache(
    session_name: Optional[str] = None,
):
    if session_name is None:
        _accounts_cache.clear()
    else:
        _accounts_cache.pop(session_name, None)

async def get_account_info(
    session_name: str,
    api_id: int,
    api_hash: str,
    use_cache: bool = True,
) -> dict:
    now = time.monotonic()

    cached = _accounts_cache.get(session_name)

    if (
        use_cache
        and cached
        and (now - cached["_cached_at"]) < CACHE_TTL
    ):
        result = dict(cached)
        result.pop("_cached_at", None)
        return result

    accounts_data = load_accounts()
    info = accounts_data.get(
        session_name,
        {},
    )

    phone = info.get(
        "phone",
        "Unknown",
    )

    async with _info_semaphore:
        try:
            session_path = SESSIONS_DIR / session_name

            async with TelegramClient(
                str(session_path),
                api_id,
                api_hash,
            ) as client:

                me = await client.get_me()

                result = {
                    "session_name": session_name,
                    "full_name": (
                        f"{me.first_name or ''} "
                        f"{me.last_name or ''}"
                    ).strip() or "Unknown",
                    "username": (
                        me.username
                        or "No username"
                    ),
                    "phone": (
                        me.phone
                        or phone
                    ),
                    "user_id": me.id,
                    "status": "Active",
                    "active": settings_manager.get(
                        session_name,
                        True,
                    ),
                }

        except AuthKeyError:
            result = {
                "session_name": session_name,
                "full_name": "Unknown",
                "username": "Unknown",
                "phone": phone,
                "user_id": "N/A",
                "status": "Logged Out",
                "active": settings_manager.get(
                    session_name,
                    True,
                ),
            }

        except RPCError as e:
            status = (
                "Banned"
                if "BANNED" in str(e).upper()
                else "Error"
            )

            result = {
                "session_name": session_name,
                "full_name": "Unknown",
                "username": "Unknown",
                "phone": phone,
                "user_id": "N/A",
                "status": status,
                "active": settings_manager.get(
                    session_name,
                    True,
                ),
            }

        except Exception:
            logger.exception(
                "Unexpected account-info error for %s",
                session_name,
            )

            result = {
                "session_name": session_name,
                "full_name": "Unknown",
                "username": "Unknown",
                "phone": phone,
                "user_id": "N/A",
                "status": "Error",
                "active": settings_manager.get(
                    session_name,
                    True,
                ),
            }

    cached_result = dict(result)
    cached_result["_cached_at"] = now
    _accounts_cache[session_name] = cached_result

    return result

async def get_all_accounts_info(
    api_id: int,
    api_hash: str,
    force_refresh: bool = False,
) -> List[dict]:
    session_names = get_session_files()

    if not session_names:
        return []

    results = await asyncio.gather(
        *(
            get_account_info(
                name,
                api_id,
                api_hash,
                use_cache=not force_refresh,
            )
            for name in session_names
        ),
        return_exceptions=True,
    )

    return [
        result
        for result in results
        if isinstance(result, dict)
    ]

# ============================================================
# 23) CLI
# ============================================================

def print_accounts_table(
    accounts_info: List[dict],
    title: str = "ACCOUNTS",
):
    clear_screen()

    box_width = 86

    print("╔" + "═" * box_width + "╗")
    print(
        f"║ {title.center(box_width - 2)} ║"
    )
    print("╚" + "═" * box_width + "╝")
    print()

    header = (
        f"{'ID':<3} │ "
        f"{'SESSION':<18} │ "
        f"{'NAME (USERNAME)':<26} │ "
        f"{'STATUS':<8} │ "
        f"{'PHONE':<12} │ "
        f"{'ACTIVE'}"
    )

    print(header)
    print("─" * len(header))

    for idx, info in enumerate(
        accounts_info,
        1,
    ):
        session = info["session_name"][:18]

        name = info.get(
            "full_name",
            "Unknown",
        )

        username = info.get(
            "username",
            "",
        )

        if username and username != "No username":
            display_name = (
                f"{name} ({username})"
            )
        else:
            display_name = name

        if len(display_name) > 26:
            display_name = (
                display_name[:23]
                + "..."
            )

        status = info.get(
            "status",
            "?",
        )

        phone = info.get(
            "phone",
            "Unknown",
        )[:12]

        active = (
            "[✅]"
            if info.get("active", True)
            else "[❌]"
        )

        print(
            f"{idx:<3} │ "
            f"{session:<18} │ "
            f"{display_name:<26} │ "
            f"{status:<8} │ "
            f"{phone:<12} │ "
            f"{active}"
        )

    print()

# ============================================================
# 24) ADD SESSION FLOW
# ============================================================

async def add_session_flow():
    if not API_ID or not API_HASH:
        return

    while True:
        purge_stale_auth_temp_sessions()
        clear_screen()

        print("╔════════════════════════════════════════╗")
        print("║      ADD NEW SESSION (SECTION 0)       ║")
        print("╚════════════════════════════════════════╝")
        print()
        print("🔐 Login sessions are temporary until Telegram authorization succeeds.")
        print("🛡️ Failed logins are deleted and never enter 01_SESSIONS.")
        print()

        phone = await async_input(
            "[?] Enter Phone Number (or 'b' to back): "
        )

        if phone.lower() == "b":
            return

        phone = phone.strip()
        if not phone:
            logger.warning("Phone number cannot be empty.")
            continue

        session_name = generate_session_filename(phone)
        device_model, os_version = get_device_model(session_name)

        logger.info(
            "Selected device profile: %s | %s",
            device_model,
            os_version,
        )

        client = create_auth_temp_client(
            session_name,
            API_ID,
            API_HASH,
        )

        login_completed = False
        two_fa_used = False

        try:
            await client.connect()
            await client.send_code_request(phone)
            logger.info("Login code sent for pending session %s", session_name)

            code = await async_input("[?] Enter code: ")

            if code.lower() == "b":
                await client.disconnect()
                delete_temp_session_files(session_name)
                return

            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                logger.info("2FA required for pending session %s", session_name)
                password = await async_input("[?] Enter 2FA password: ")

                if password.lower() == "b":
                    await client.disconnect()
                    delete_temp_session_files(session_name)
                    return

                await client.sign_in(password=password)
                two_fa_used = True

            if not await client.is_user_authorized():
                raise RuntimeError(
                    "Telegram did not authorize the account."
                )

            login_completed = True
            logger.info(
                "Login fully authorized for session %s",
                session_name,
            )

        except FloodWaitError as e:
            logger.warning(
                "FloodWait while adding %s: %s seconds",
                session_name,
                e.seconds,
            )
            print(
                f"⏳ Telegram rate-limited this login for {e.seconds} seconds."
            )

        except Exception:
            logger.exception(
                "Login failed for pending session %s",
                session_name,
            )
            print("❌ Login failed. Temporary session will be removed.")

        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

        if not login_completed:
            delete_temp_session_files(session_name)
            again = await async_input("Add another? (y/n): ")
            if again.strip().lower() != "y":
                return
            continue

        if not promote_authenticated_session(session_name):
            print("❌ Login succeeded but session finalization failed.")
            delete_temp_session_files(session_name)
            delete_session_files(session_name)
            again = await async_input("Add another? (y/n): ")
            if again.strip().lower() != "y":
                return
            continue

        acc_data = {
            "phone": phone,
            "is_active": True,
            "device": device_model,
            "os": os_version,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        acc_data["two_fa_enabled"] = two_fa_used

        upsert_account(session_name, acc_data)
        settings_manager.set(session_name, True)
        invalidate_account_cache(session_name)

        backup_ok = await backup_session_on_login(
            session_name,
            phone,
            API_ID,
            API_HASH,
        )

        print()
        print("✅ Login completed successfully.")
        print("✅ Session moved to 01_SESSIONS.")
        print(f"☁️ Session backup: {'✅' if backup_ok else '⚠️'}")

        again = await async_input("Add another? (y/n): ")
        if again.strip().lower() != "y":
            return

# ============================================================
# 25) TOGGLE / DELETE MENUS
# ============================================================

def parse_selection(
    selection: str,
    max_index: int,
) -> List[int]:
    selection = selection.strip().lower()

    if selection == "all":
        return list(range(1, max_index + 1))

    selected = []

    for part in selection.split(","):
        part = part.strip()

        if not part:
            continue

        if "-" in part:
            try:
                start, end = map(
                    int,
                    part.split("-", 1),
                )
                selected.extend(
                    range(start, end + 1)
                )
            except ValueError:
                continue
        else:
            try:
                selected.append(
                    int(part)
                )
            except ValueError:
                continue

    return sorted(
        set(
            i for i in selected
            if 1 <= i <= max_index
        )
    )

async def toggle_active_menu(
    accounts_info: List[dict],
):
    while True:
        print_accounts_table(
            accounts_info,
            title="TOGGLE ACTIVE STATUS",
        )

        print("[q] Back to Manager")
        print()

        selection = await async_input(
            "Select Account IDs to toggle "
            "(e.g., 1,3-5 or 'all' or 'q'): "
        )

        if selection.lower() in (
            "q",
            "cancel",
        ):
            return

        selected_indices = parse_selection(
            selection,
            len(accounts_info),
        )

        if not selected_indices:
            logger.warning(
                "No valid accounts selected."
            )
            await async_input(
                "Press Enter to continue..."
            )
            continue

        toggled = []

        for idx in selected_indices:
            info = accounts_info[idx - 1]
            name = info["session_name"]

            new_status = settings_manager.toggle(
                name
            )

            invalidate_account_cache(name)

            info["active"] = new_status

            toggled.append(
                f"{name} → "
                f"{'✅ Active' if new_status else '❌ Inactive'}"
            )

        clear_screen()

        print("✅ Toggled:")

        for item in toggled:
            print(f"  {item}")

        await async_input(
            "Press Enter to continue..."
        )

async def delete_session_menu(
    accounts_info: List[dict],
):
    while True:
        print_accounts_table(
            accounts_info,
            title="DELETE SESSIONS",
        )

        print("[q] Back to Manager")
        print()

        selection = await async_input(
            "Select Account IDs to delete "
            "(e.g., 1,3-5 or 'all' or 'q'): "
        )

        if selection.lower() in (
            "q",
            "cancel",
        ):
            return

        selected_indices = parse_selection(
            selection,
            len(accounts_info),
        )

        if not selected_indices:
            logger.warning(
                "No valid accounts selected."
            )
            await async_input(
                "Press Enter to continue..."
            )
            continue

        deleted = []

        for idx in selected_indices:
            info = accounts_info[idx - 1]
            name = info["session_name"]

            if client_manager.get(name):
                await client_manager.remove(name)

            delete_session_files(name)
            remove_account(name)
            settings_manager.remove(name)
            invalidate_account_cache(name)

            deleted.append(name)

        clear_screen()

        print("🗑 Deleted sessions:")

        for name in deleted:
            print(f"  ❌ {name}")

        accounts_info[:] = [
            info
            for info in accounts_info
            if info["session_name"]
            not in deleted
        ]

        if not accounts_info:
            logger.info(
                "All sessions deleted."
            )
            await async_input(
                "Press Enter to continue..."
            )
            return

        await async_input(
            "Press Enter to continue..."
        )

# ============================================================
# 26) BACKUP CHANNEL MENU
# ============================================================

async def set_backup_channel():
    clear_screen()

    print("--- Set Backup Channel ---")
    print(
        "Enter the username (e.g., @my_channel) "
        "or numeric ID of the channel."
    )
    print(
        "(The bot must have access to the channel.)"
    )
    print("Type 'q' to cancel.")

    channel_input = await async_input(
        "Channel: "
    )

    if channel_input.lower() == "q":
        return

    channel_input = channel_input.strip()

    if not channel_input:
        print("❌ Invalid input.")
        await async_input(
            "Press Enter to continue..."
        )
        return

    config = ConfigManager.load(CONFIG_FILE)
    config["backup_channel"] = channel_input
    ConfigManager.save(CONFIG_FILE, config)

    global backup_channel_entity
    global backup_channel_input_cached

    backup_channel_entity = None
    backup_channel_input_cached = None

    print(
        f"✅ Backup channel set to: "
        f"{channel_input}"
    )

    await async_input(
        "Press Enter to continue..."
    )

# ============================================================
# 27) MANAGER
# ============================================================

async def manager_menu():
    global backup_channel_entity
    global backup_channel_input_cached

    if not API_ID or not API_HASH:
        return

    settings_manager.sync_with_sessions()

    while True:
        accounts_info = await get_all_accounts_info(
            API_ID,
            API_HASH,
        )

        if not accounts_info:
            logger.warning(
                "No sessions found."
            )
            await async_input(
                "Press Enter to continue..."
            )
            return

        print_accounts_table(
            accounts_info,
            title="MANAGER - ACCOUNTS",
        )

        backup_configured = get_configured_backup_channel() is not None

        print("─" * 50)

        status_text = (
            "🟢 RUNNING"
            if is_bot_running
            else "🔴 STOPPED"
        )

        print(
            f"🤖 Bot Status: {status_text}"
        )

        print(
            f"🗂️ Storage: {'🟢 Ready' if backup_configured else '🟡 Setup needed'}"
        )

        print("─" * 50)

        print(
            "[b] Toggle Active    "
            "[d] Delete Session    "
            "[s] Start Bot    [t] Stop Bot"
        )

        print(
            "[q] Main Menu"
        )

        print()

        choice = (
            await async_input("Select: ")
        ).strip().lower()

        if choice == "b":
            await toggle_active_menu(
                accounts_info
            )

        elif choice == "d":
            await delete_session_menu(
                accounts_info
            )

        elif choice == "s":
            await start_bot()

        elif choice == "t":
            await stop_bot()

        elif choice == "q":
            break

        else:
            logger.warning(
                "Invalid choice."
            )
            await asyncio.sleep(1)

# ============================================================
# 28) SHUTDOWN
# ============================================================

async def shutdown():
    logger.info("Shutting down...")

    await stop_bot(quiet=True)

    for chat_id in list(remote_state.keys()):
        await clear_remote_state(chat_id)

    await close_bot_session()

    logger.info("Shutdown complete.")

# ============================================================
# 29) SERV00 ENTRYPOINT
# ============================================================

async def drive_sync_loop():
    if not GOOGLE_DRIVE_ENABLED:
        return

    interval = max(
        30,
        int(os.getenv("GOOGLE_DRIVE_SYNC_INTERVAL", "60")),
    )

    while True:
        try:
            await asyncio.to_thread(drive_sync.sync_up)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Drive sync loop error.")

        await asyncio.sleep(interval)


async def run():
    load_runtime_secrets()

    if GOOGLE_DRIVE_ENABLED:
        if not await asyncio.to_thread(drive_sync.pull):
            raise RuntimeError(
                "Google Drive synchronization failed; refusing to start "
                "with incomplete persistent state."
            )

    settings_manager._load()
    settings_manager.sync_with_sessions()
    print_drive_layout()

    sync_task = None
    if GOOGLE_DRIVE_ENABLED:
        sync_task = asyncio.create_task(
            drive_sync_loop(),
            name="google-drive-sync",
        )

    try:
        await start_bot()
        await start_remote_bot()
    except asyncio.CancelledError:
        raise
    except KeyboardInterrupt:
        logger.info("Interrupted by process manager.")
    finally:
        if sync_task is not None:
            sync_task.cancel()
            await asyncio.gather(
                sync_task,
                return_exceptions=True,
            )

        await shutdown()

        if GOOGLE_DRIVE_ENABLED:
            await asyncio.to_thread(
                drive_sync.sync_up,
                True,
            )


if __name__ == "__main__":
    asyncio.run(run())
