# -*- coding: utf-8 -*-
"""Remote aiogram admin bot and login flows."""

from .shared import *
from . import core
from .core import *
from .account_info import invalidate_account_cache

# 21) REMOTE BOT — QR LOGIN EDITION
# ============================================================

async def start_remote_bot():
    if not core.REMOTE_BOT_TOKEN:
        logger.info("Remote bot disabled because no token was supplied.")
        return

    if not core.API_ID or not core.API_HASH:
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
        token=core.REMOTE_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    logger.info("Remote UI: aiogram initialized for TelegramMaster V14.5-fixed18.")

    def is_private_message(message: Message) -> bool:
        return bool(message.chat and message.chat.type == "private")

    def is_admin(user_id: int) -> bool:
        return user_id in core.REMOTE_ADMIN_IDS

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
        return "🟢 آماده" if core.is_bot_running else "🟡 متوقف"

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
                core.API_ID,
                core.API_HASH,
            )
        except Exception:
            logger.exception("Post-login backup operation failed for %s", session_name)

        hot_added = False
        if core.is_bot_running:
            hot_added = await hot_add_account(session_name, core.API_ID, core.API_HASH)

        connection_text = "🟢 آماده استفاده" if (hot_added or not core.is_bot_running) else "🟡 ثبت شد"
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
                    current = core.remote_state.get(chat_id)
                    if current is state:
                        core.remote_state.pop(chat_id, None)

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
            current = core.remote_state.get(chat_id)
            if current is state:
                core.remote_state.pop(chat_id, None)

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
        
        if core.is_bot_running:
            return True
        settings_manager.sync_with_sessions()
        active_sessions = [
            name for name in get_session_files()
            if settings_manager.get(name, True)
        ]
        if not active_sessions:
            return False
        results = await asyncio.gather(
            *(start_single_client(name, core.API_ID, core.API_HASH) for name in active_sessions),
            return_exceptions=True,
        )
        connected = [r for r in results if isinstance(r, TelegramClient)]
        core.is_bot_running = bool(connected)
        return core.is_bot_running

    async def stop_clients_silent() -> None:
        
        await client_manager.stop_all()
        core.is_bot_running = False

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
        client = create_auth_temp_client(session_name, core.API_ID, core.API_HASH)
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
                core.remote_state[chat_id] = state
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
            core.remote_state[chat_id] = {
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
            state = core.remote_state.get(chat_id)
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
            state = core.remote_state.get(chat_id)
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
            state = core.remote_state.get(chat_id)
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
                    current = core.remote_state.get(chat_id)
                    if current is state:
                        core.remote_state.pop(chat_id, None)

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
                current = core.remote_state.get(chat_id)
                if current is state:
                    core.remote_state.pop(chat_id, None)

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
            state = core.remote_state.get(chat_id)
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
        client = create_auth_temp_client(session_name, core.API_ID, core.API_HASH)
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
        for chat_id in list(core.remote_state.keys()):
            await clear_remote_state(chat_id)
        await bot.session.close()

# ============================================================
