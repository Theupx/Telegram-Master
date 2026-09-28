# -*- coding: utf-8 -*-
"""CLI and maintenance menus."""

from .shared import *
from . import core
from .core import *
from .account_info import get_all_accounts_info, print_accounts_table, invalidate_account_cache

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
    if not core.API_ID or not core.API_HASH:
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
            core.API_ID,
            core.API_HASH,
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
            core.API_ID,
            core.API_HASH,
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

    
    

    core.backup_channel_entity = None
    core.backup_channel_input_cached = None

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
    
    

    if not core.API_ID or not core.API_HASH:
        return

    settings_manager.sync_with_sessions()

    while True:
        accounts_info = await get_all_accounts_info(
            core.API_ID,
            core.API_HASH,
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
            if core.is_bot_running
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
