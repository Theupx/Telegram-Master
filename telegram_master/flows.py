# -*- coding: utf-8 -*-
"""Interactive account/session addition flow."""

from .shared import *
from . import core
from .core import *
from .account_info import invalidate_account_cache

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
