# -*- coding: utf-8 -*-
"""Account information cache and reporting."""

from .shared import *
from .core import *

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
