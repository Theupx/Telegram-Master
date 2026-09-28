# -*- coding: utf-8 -*-
"""Telethon user-account event handlers."""

from .shared import *
from . import core
from .core import *

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
            if sender_id not in core.REMOTE_ADMIN_IDS:
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
