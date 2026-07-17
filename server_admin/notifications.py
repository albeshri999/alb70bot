# -*- coding: utf-8 -*-
"""
server_admin.notifications — everything about telling the admin what's
happening and recording that it happened:
  - rule #6:  '⌛ جاري التنفيذ...' → '✅ اكتملت' / '❌ فشلت' for every action.
  - rule #8:  results over ~3500 characters are sent as a .txt file instead.
  - rule #10: every action is logged to server_admin.log (time, admin name,
              Telegram ID, action, duration, result).
  - rule #11: a single global lock ensures only one action ever runs at a
              time — a second tap while busy is rejected outright.
"""
import asyncio
import logging
import os
import time
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from server_admin.utils import MAX_MESSAGE_LEN, timestamp

# ── Audit log (server_admin.log) ────────────────────────────────────────────
LOG_FILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server_admin.log"
)
logger = logging.getLogger(__name__)

_audit_logger = logging.getLogger("server_admin_audit")
_audit_logger.setLevel(logging.INFO)
if not _audit_logger.handlers:
    _fh = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    _audit_logger.addHandler(_fh)
    _audit_logger.propagate = False


def log_action(update: Update, title: str, duration: float, success: bool) -> None:
    user = update.effective_user
    admin_name = user.full_name if user else "—"
    admin_id = user.id if user else "—"
    result = "SUCCESS" if success else "FAILED"
    _audit_logger.info(
        "admin=%s | id=%s | action=%s | duration=%.2fs | result=%s",
        admin_name, admin_id, title, duration, result,
    )


# ── Concurrency guard (rule #11) ────────────────────────────────────────────
_busy_lock = None


def get_lock() -> asyncio.Lock:
    global _busy_lock
    if _busy_lock is None:
        _busy_lock = asyncio.Lock()
    return _busy_lock


async def send_long_or_short(message_obj, full_text: str, title: str, keyboard):
    """Edits the progress message with the final result, or — if the result
    is too long for one Telegram message (rule #8) — sends it as a .txt
    file instead (via send_document) and leaves a short pointer behind."""
    if len(full_text) > MAX_MESSAGE_LEN:
        safe_name = "".join(c for c in title if c.isalnum()) or "result"
        tmp_path = f"/tmp/{safe_name}-{timestamp()}.txt"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(full_text)
            with open(tmp_path, "rb") as f:
                await message_obj.reply_document(document=f, filename=os.path.basename(tmp_path), caption=title)
        except Exception as e:
            logger.error("server_admin.notifications: failed to send result as file: %s", e)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        try:
            await message_obj.edit_text(f"{title}\n\nتم إرسال النتيجة كملف نصي (طويلة جداً).", reply_markup=keyboard)
        except Exception:
            pass
    else:
        try:
            await message_obj.edit_text(full_text, reply_markup=keyboard)
        except Exception:
            await message_obj.reply_text(full_text, reply_markup=keyboard)


async def execute_with_progress(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, action_func):
    """The generic 'run an action with progress → result' wrapper used by
    every text-report action (rule #6): sends '⌛ جاري التنفيذ...' first,
    runs the (blocking) action in a worker thread so the bot stays
    responsive, then edits that same message with '✅ اكتملت'/'❌ فشلت' plus
    the output — and logs the whole thing to server_admin.log (rule #10)."""
    lock = get_lock()
    if lock.locked():
        if update.callback_query:
            await update.callback_query.answer("⏳ توجد عملية أخرى قيد التنفيذ، الرجاء الانتظار.", show_alert=True)
        return
    async with lock:
        if update.callback_query:
            await update.callback_query.answer()
        message = update.callback_query.message if update.callback_query else update.message
        progress = await message.reply_text(f"⌛ جاري التنفيذ...\n\n{title}")

        start = time.monotonic()
        try:
            success, output = await asyncio.to_thread(action_func)
        except Exception as e:
            success, output = False, f"⚠️ خطأ غير متوقع:\n{e}"
        duration = time.monotonic() - start

        log_action(update, title, duration, success)

        result_line = "✅ اكتملت" if success else "❌ فشلت"
        full_text = f"{title}\n\n{result_line} (المدة: {duration:.1f} ثانية)\n\n{output or '(لا يوجد ناتج)'}"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للوحة السيرفر", callback_data="srv_menu")]])
        await send_long_or_short(progress, full_text, title, kb)


async def execute_download(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, build_func):
    """Like execute_with_progress, but for actions that produce a FILE
    (project/database/log download) instead of a text report."""
    lock = get_lock()
    if lock.locked():
        await update.callback_query.answer("⏳ توجد عملية أخرى قيد التنفيذ، الرجاء الانتظار.", show_alert=True)
        return
    async with lock:
        await update.callback_query.answer()
        message = update.callback_query.message
        progress = await message.reply_text(f"⌛ جاري التنفيذ...\n\n{title}")

        start = time.monotonic()
        try:
            success, out, filepath = await asyncio.to_thread(build_func)
        except Exception as e:
            success, out, filepath = False, f"⚠️ خطأ غير متوقع:\n{e}", None
        duration = time.monotonic() - start

        log_action(update, title, duration, success)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للوحة السيرفر", callback_data="srv_menu")]])

        if success and filepath and os.path.exists(filepath):
            try:
                with open(filepath, "rb") as f:
                    await message.reply_document(document=f, filename=os.path.basename(filepath),
                                                  caption=f"{title}\n✅ اكتملت")
            except Exception as e:
                logger.error("server_admin.notifications: download send failed: %s", e)
            finally:
                try:
                    os.remove(filepath)
                except Exception:
                    pass
            try:
                await progress.edit_text(f"{title}\n\n✅ اكتملت (المدة: {duration:.1f} ثانية) — أُرسل الملف أعلاه.",
                                          reply_markup=kb)
            except Exception:
                pass
        else:
            full_text = f"{title}\n\n❌ فشلت (المدة: {duration:.1f} ثانية)\n\n{out or '(لا يوجد ناتج)'}"
            await send_long_or_short(progress, full_text, title, kb)
