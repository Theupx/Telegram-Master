# -*- coding: utf-8 -*-
"""Application entry point."""

from .shared import *
from . import core
from .core import *
from . import remote
from .remote import start_remote_bot

# 28) SHUTDOWN
# ============================================================

async def shutdown():
    logger.info("Shutting down...")

    await stop_bot(quiet=True)

    for chat_id in list(core.remote_state.keys()):
        await clear_remote_state(chat_id)

    await close_bot_session()

    logger.info("Shutdown complete.")

# ============================================================
# 29) SERV00 ENTRYPOINT
# ============================================================

async def drive_sync_loop():
    if not core.GOOGLE_DRIVE_ENABLED:
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

    if core.GOOGLE_DRIVE_ENABLED:
        if not await asyncio.to_thread(drive_sync.pull):
            raise RuntimeError(
                "Google Drive synchronization failed; refusing to start "
                "with incomplete persistent state."
            )

    settings_manager._load()
    settings_manager.sync_with_sessions()
    print_drive_layout()

    sync_task = None
    if core.GOOGLE_DRIVE_ENABLED:
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

        if core.GOOGLE_DRIVE_ENABLED:
            await asyncio.to_thread(
                drive_sync.sync_up,
                True,
            )


if __name__ == "__main__":
    asyncio.run(run())
