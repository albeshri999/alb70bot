# -*- coding: utf-8 -*-
"""
server_admin.backup — 💾 إنشاء نسخة احتياطية / 📂 عرض النسخ الاحتياطية /
🗑 حذف النسخ القديمة, plus the project/database download archives.

The backup BROWSER (list → per-file ↩ استرجاع / 🗑 حذف) lives here too since
it's purely a backup-management concern; it calls into rollback.py only for
the actual "restore this file" action.
"""
import os
import time
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from server_admin.utils import (
    run_command, reply, is_owner, fmt_size, timestamp, list_backup_files,
    create_backup_archive, BOT_DIR, BACKUPS_DIR, KEEP_BACKUPS,
    NO_PERMISSION_TEXT, SRV_MENU, SRV_BACKUP_LIST, SRV_BACKUP_ITEM, SRV_BACKUP_CONFIRM,
)
from server_admin.notifications import execute_with_progress, log_action
from server_admin.rollback import act_restore_backup


# ── Plain actions (routed through the generic execute_with_progress) ───────

def act_backup():
    ok, report, _path = create_backup_archive(prefix="manual")
    return ok, report


def act_cleanup():
    """🗑 حذف النسخ القديمة — deletes every backup file except the most
    recent KEEP_BACKUPS ones, implemented directly in Python (not a shell
    pipeline) for safety and predictability."""
    try:
        if not os.path.isdir(BACKUPS_DIR):
            return True, "📂 لا يوجد مجلد نسخ احتياطية بعد — لا شيء لتنظيفه."
        files = list_backup_files()
        to_delete = files[KEEP_BACKUPS:]
        if not to_delete:
            return True, f"✅ لا توجد نسخ زائدة عن آخر {KEEP_BACKUPS} نسخ. لم يُحذف شيء."
        deleted, failed = [], []
        for f in to_delete:
            try:
                os.remove(f)
                deleted.append(os.path.basename(f))
            except Exception as e:
                failed.append(f"{os.path.basename(f)} ({e})")
        lines = [f"🗑 تم حذف {len(deleted)} نسخة قديمة:"]
        lines.extend(f"  • {name}" for name in deleted)
        if failed:
            lines.append("\n⚠️ فشل حذف:")
            lines.extend(f"  • {name}" for name in failed)
        return (len(failed) == 0), "\n".join(lines)
    except Exception as e:
        return False, f"⚠️ حدث خطأ غير متوقع أثناء التنظيف:\n{e}"


# ── Downloads (return (success, message, filepath_or_None)) ────────────────

def build_project_archive():
    parent = os.path.dirname(BOT_DIR.rstrip("/")) or "/"
    base = os.path.basename(BOT_DIR.rstrip("/"))
    tmp_path = f"/tmp/project-{timestamp()}.tar.gz"
    ok, out = run_command([
        "tar", "-czf", tmp_path,
        "--exclude=venv", "--exclude=__pycache__", "--exclude=.git", "--exclude=backups",
        "-C", parent, base,
    ])
    return ok, out, (tmp_path if ok and os.path.exists(tmp_path) else None)


def build_db_archive():
    data_dir = os.path.join(BOT_DIR, "data")
    if not os.path.isdir(data_dir):
        return False, "⚠️ مجلد data غير موجود.", None
    tmp_path = f"/tmp/database-{timestamp()}.tar.gz"
    ok, out = run_command(["tar", "-czf", tmp_path, "-C", BOT_DIR, "data"])
    return ok, out, (tmp_path if ok and os.path.exists(tmp_path) else None)


# ── Interactive backup browser ───────────────────────────────────────────────

async def srv_backup_list_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    await query.answer()

    files = list_backup_files()
    context.user_data["srv_backup_files"] = files
    if not files:
        await reply(
            update, "📂 لا توجد نسخ احتياطية بعد.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="srv_menu")]]),
        )
        return SRV_BACKUP_LIST

    rows = []
    for i, f in enumerate(files):
        size = fmt_size(os.path.getsize(f))
        mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")
        label = f"{os.path.basename(f)} ({size}, {mtime})"
        rows.append([InlineKeyboardButton(label[:64], callback_data=f"srv_bk_{i}")])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="srv_menu")])
    await reply(update, "📂 *النسخ الاحتياطية*\n\nاختر نسخة:", InlineKeyboardMarkup(rows))
    return SRV_BACKUP_LIST


async def srv_backup_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    idx = int(query.data.replace("srv_bk_", ""))
    files = context.user_data.get("srv_backup_files", [])
    if idx >= len(files):
        await query.answer("⚠️ لم يعد بالإمكان العثور على هذه النسخة.", show_alert=True)
        return await srv_backup_list_show(update, context)
    await query.answer()
    context.user_data["srv_backup_idx"] = idx
    f = files[idx]
    size = fmt_size(os.path.getsize(f))
    mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")
    text = f"💾 *{os.path.basename(f)}*\n\nالحجم: {size}\nالتاريخ: {mtime}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("↩ استرجاع", callback_data=f"srv_bk_restore_{idx}")],
        [InlineKeyboardButton("🗑 حذف", callback_data=f"srv_bk_delete_{idx}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="srv_list_backups")],
    ])
    await reply(update, text, kb)
    return SRV_BACKUP_ITEM


async def srv_backup_restore_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rule #7 — restore is a dangerous operation, so it always asks first."""
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    idx = int(query.data.replace("srv_bk_restore_", ""))
    files = context.user_data.get("srv_backup_files", [])
    if idx >= len(files):
        await query.answer("⚠️ لم يعد بالإمكان العثور على هذه النسخة.", show_alert=True)
        return await srv_backup_list_show(update, context)
    await query.answer()
    fname = os.path.basename(files[idx])
    await reply(
        update,
        f"⚠️ هل أنت متأكد من استرجاع هذه النسخة؟\n\n{fname}\n\n"
        "سيتم إيقاف البوت مؤقتاً أثناء الاسترجاع ثم إعادة تشغيله تلقائياً.",
        InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ نعم", callback_data=f"srv_bk_restore_yes_{idx}"),
            InlineKeyboardButton("❌ إلغاء", callback_data=f"srv_bk_{idx}"),
        ]]),
    )
    return SRV_BACKUP_CONFIRM


async def srv_backup_restore_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    idx = int(query.data.replace("srv_bk_restore_yes_", ""))
    files = context.user_data.get("srv_backup_files", [])
    if idx >= len(files):
        await query.answer("⚠️ لم يعد بالإمكان العثور على هذه النسخة.", show_alert=True)
        return await srv_backup_list_show(update, context)
    filepath = files[idx]
    title = f"↩ استرجاع نسخة: {os.path.basename(filepath)}"
    await execute_with_progress(update, context, title, lambda: act_restore_backup(filepath))
    return SRV_MENU


async def srv_backup_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rule #7 — delete is a dangerous operation, so it always asks first."""
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    idx = int(query.data.replace("srv_bk_delete_", ""))
    files = context.user_data.get("srv_backup_files", [])
    if idx >= len(files):
        await query.answer("⚠️ لم يعد بالإمكان العثور على هذه النسخة.", show_alert=True)
        return await srv_backup_list_show(update, context)
    await query.answer()
    fname = os.path.basename(files[idx])
    await reply(
        update,
        f"⚠️ هل أنت متأكد من حذف هذه النسخة؟\n\n{fname}",
        InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 نعم، حذف", callback_data=f"srv_bk_delete_yes_{idx}"),
            InlineKeyboardButton("❌ إلغاء", callback_data=f"srv_bk_{idx}"),
        ]]),
    )
    return SRV_BACKUP_CONFIRM


async def srv_backup_delete_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    idx = int(query.data.replace("srv_bk_delete_yes_", ""))
    files = context.user_data.get("srv_backup_files", [])
    await query.answer()
    if idx >= len(files):
        await reply(update, "⚠️ لم يعد بالإمكان العثور على هذه النسخة.",
                    InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="srv_menu")]]))
        return SRV_MENU
    filepath = files[idx]
    fname = os.path.basename(filepath)
    start = time.monotonic()
    try:
        os.remove(filepath)
        success, msg = True, f"🗑 تم حذف: {fname}"
    except Exception as e:
        success, msg = False, f"⚠️ فشل حذف {fname}:\n{e}"
    duration = time.monotonic() - start
    log_action(update, f"🗑 حذف نسخة: {fname}", duration, success)
    result_line = "✅ اكتملت" if success else "❌ فشلت"
    await reply(
        update, f"{result_line}\n\n{msg}",
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للوحة السيرفر", callback_data="srv_menu")]]),
    )
    return SRV_MENU
