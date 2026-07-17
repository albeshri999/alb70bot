# -*- coding: utf-8 -*-
"""
server_admin.zip_update — 📤 رفع تحديث ZIP: the primary way to update the
bot entirely from Telegram (no SSH/terminal/FileZilla needed), per the
project's "Claude + Telegram only" philosophy.

Flow: admin taps "📤 رفع تحديث ZIP" → sends a .zip document → bot inspects
it (name/size/entry count) and offers ✅ تثبيت / 📋 عرض المحتويات /
❌ إلغاء → on ✅ تثبيت: automatic backup → extract to a temp staging area →
validate it's a real project (main.py + requirements.txt present) →
compare against the live project (new/modified/deleted counts) → mirror the
files in → pip install if requirements.txt changed → compileall → restart →
health-check → success, or automatic rollback to the backup on any failure.

Everything here is fully independent of the competition system — it only
ever touches BOT_DIR's own files and the backups it creates itself.
"""
import asyncio
import filecmp
import os
import shutil
import time
import zipfile

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from server_admin.utils import (
    is_owner, reply, fmt_size, timestamp, run_command, create_backup_archive,
    BOT_DIR, SERVICE_NAME, MAX_MESSAGE_LEN, NO_PERMISSION_TEXT,
    SRV_MENU, SRV_UPDATE_MENU, SRV_UPDATE_AWAIT_ZIP, SRV_UPDATE_ZIP_REVIEW,
)
from server_admin.health import check_service_health
from server_admin.rollback import act_restore_backup
from server_admin.notifications import get_lock, log_action, get_recent_log_entries

UPDATE_ZIP_TITLE = "📤 تحديث عبر ZIP"
GITHUB_DEPLOY_TITLE = "🌿 تثبيت من GitHub"

TMP_BASE = "/tmp/server_admin_zip_update"

# Never touched/replaced/deleted by the mirror-copy step, and never counted
# in the new/modified/deleted comparison.
EXCLUDE_DIR_NAMES = {".git", "venv", "logs", "backups", "__pycache__", ".idea", ".vscode", ".pytest_cache"}
EXCLUDE_FILE_SUFFIXES = (".log", ".pyc")
REQUIRED_PROJECT_FILES = ("main.py", "requirements.txt")


def _is_excluded_dir(name: str) -> bool:
    return name in EXCLUDE_DIR_NAMES


def _is_excluded_file(name: str) -> bool:
    return name.endswith(EXCLUDE_FILE_SUFFIXES)


def _walk_project_files(root: str) -> set:
    """Every file path (relative to `root`) that the update pipeline
    considers part of the project — i.e. excluding the directories/patterns
    that must never be touched."""
    result = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _is_excluded_dir(d)]
        rel_dir = os.path.relpath(dirpath, root)
        for name in filenames:
            if _is_excluded_file(name):
                continue
            rel_path = name if rel_dir == "." else os.path.join(rel_dir, name)
            result.add(rel_path)
    return result


def _locate_project_root(extract_dir: str):
    """Handles the common case where the zip contains one wrapping folder
    (e.g. 'myproject/main.py') instead of the project files directly at the
    zip's root. Returns the effective root directory, or None if no valid
    project (main.py + requirements.txt) can be found at all."""
    def _has_required_files(path):
        return all(os.path.isfile(os.path.join(path, f)) for f in REQUIRED_PROJECT_FILES)

    if _has_required_files(extract_dir):
        return extract_dir

    entries = [e for e in os.listdir(extract_dir) if not e.startswith("__MACOSX")]
    real_dirs = [e for e in entries if os.path.isdir(os.path.join(extract_dir, e))]
    if len(entries) == 1 and len(real_dirs) == 1:
        candidate = os.path.join(extract_dir, real_dirs[0])
        if _has_required_files(candidate):
            return candidate
    return None


def _compare_trees(source_root: str, live_root: str):
    """Returns (new_files, modified_files, deleted_files) — lists of
    relative paths — comparing the new project tree against the live one,
    ignoring anything in EXCLUDE_DIR_NAMES/EXCLUDE_FILE_SUFFIXES on both
    sides."""
    source_files = _walk_project_files(source_root)
    live_files = _walk_project_files(live_root) if os.path.isdir(live_root) else set()

    new_files = sorted(source_files - live_files)
    deleted_files = sorted(live_files - source_files)
    common = source_files & live_files

    modified_files = []
    for rel in sorted(common):
        src_path = os.path.join(source_root, rel)
        live_path = os.path.join(live_root, rel)
        try:
            if not filecmp.cmp(src_path, live_path, shallow=False):
                modified_files.append(rel)
        except Exception:
            modified_files.append(rel)
    return new_files, modified_files, deleted_files


def _mirror_copy(source_root: str, live_root: str):
    """Copies every project file from source_root over live_root, creating
    directories as needed, then deletes any live project file that's no
    longer present in source_root — an exact mirror of the new version,
    while NEVER touching EXCLUDE_DIR_NAMES/EXCLUDE_FILE_SUFFIXES on the live
    side (so .git/venv/logs/backups/__pycache__/.idea/.vscode survive
    untouched, exactly as required)."""
    os.makedirs(live_root, exist_ok=True)
    source_files = _walk_project_files(source_root)
    live_files_before = _walk_project_files(live_root)

    for rel in sorted(source_files):
        src_path = os.path.join(source_root, rel)
        dst_path = os.path.join(live_root, rel)
        os.makedirs(os.path.dirname(dst_path) or live_root, exist_ok=True)
        shutil.copy2(src_path, dst_path)

    for rel in sorted(live_files_before - source_files):
        dst_path = os.path.join(live_root, rel)
        try:
            os.remove(dst_path)
        except FileNotFoundError:
            pass

    # Clean up any now-empty directories left behind by deleted files (but
    # never remove the excluded/protected directories themselves).
    for dirpath, dirnames, filenames in os.walk(live_root, topdown=False):
        dirnames[:] = [d for d in dirnames if not _is_excluded_dir(d)]
        if dirpath == live_root:
            continue
        base = os.path.basename(dirpath)
        if _is_excluded_dir(base):
            continue
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
        except OSError:
            pass


def _requirements_changed(source_root: str, live_root: str) -> bool:
    src_req = os.path.join(source_root, "requirements.txt")
    live_req = os.path.join(live_root, "requirements.txt")
    if not os.path.isfile(live_req):
        return os.path.isfile(src_req)
    if not os.path.isfile(src_req):
        return False
    try:
        return not filecmp.cmp(src_req, live_req, shallow=False)
    except Exception:
        return True


def _run_zip_install_pipeline(zip_path: str, session_dir: str):
    """The full install pipeline, run in a worker thread. Returns
    (success, report_text, backup_name_or_None, file_count)."""
    extract_dir = os.path.join(session_dir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
    except Exception as e:
        return False, f"⚠️ فشل فك ضغط الأرشيف:\n{e}", None, 0

    source_root = _locate_project_root(extract_dir)
    if not source_root:
        required = "، ".join(REQUIRED_PROJECT_FILES)
        return False, f"❌ الملف المضغوط لا يحتوي على مشروع صالح.\n\nيجب أن يحتوي على الأقل على: {required}", None, 0

    # Backup FIRST, before touching anything live.
    backup_ok, backup_out, backup_path = create_backup_archive(prefix="zipupdate")
    if not backup_ok:
        return False, f"⚠️ فشل إنشاء نسخة احتياطية — تم إلغاء التثبيت لحماية النظام.\n\n{backup_out}", None, 0
    backup_name = os.path.basename(backup_path) if backup_path else None

    def _auto_rollback(reason: str, report_so_far: str, file_count: int):
        rb_ok, rb_out = act_restore_backup(backup_path)
        note = "✅ تم استرجاع النسخة السابقة بنجاح." if rb_ok else "❌ فشل الاسترجاع التلقائي أيضاً! يتطلب تدخلاً يدوياً فورياً."
        full = f"{report_so_far}\n\n⚠️ {reason} — سيتم الاسترجاع تلقائياً...\n\n{rb_out}\n\n{note}"
        return False, full, backup_name, file_count

    new_files, modified_files, deleted_files = _compare_trees(source_root, BOT_DIR)
    requirements_changed = _requirements_changed(source_root, BOT_DIR)
    file_count = len(new_files) + len(modified_files)
    comparison_report = (
        f"📥 ملفات جديدة: {len(new_files)}\n"
        f"✏️ ملفات معدّلة: {len(modified_files)}\n"
        f"🗑 ملفات محذوفة: {len(deleted_files)}"
    )

    try:
        _mirror_copy(source_root, BOT_DIR)
    except Exception as e:
        return _auto_rollback(f"فشل استبدال الملفات:\n{e}", comparison_report, file_count)

    report = comparison_report

    if requirements_changed:
        cmd = f"cd {BOT_DIR} && source venv/bin/activate && pip install -r requirements.txt"
        pip_ok, pip_out = run_command(cmd, timeout=300, shell=True)
        report += f"\n\n── تثبيت المتطلبات (تغيّر requirements.txt) ──\n{pip_out}"
        if not pip_ok:
            return _auto_rollback("فشل تثبيت المتطلبات (pip install)", report, file_count)

    compile_ok, compile_out = run_command(["python3", "-m", "compileall", "."], timeout=120, cwd=BOT_DIR)
    report += f"\n\n── فحص التجميع (compileall) ──\n{compile_out}"
    if not compile_ok:
        return _auto_rollback("فشل فحص تجميع الملفات (compileall)", report, file_count)

    restart_ok, restart_out = run_command(["systemctl", "restart", SERVICE_NAME], timeout=60)
    report += f"\n\n── إعادة تشغيل الخدمة ──\n{restart_out}"

    health_ok, health_out = check_service_health()
    report += f"\n\n── فحص الحالة (Health Check) ──\n{health_out}"

    if restart_ok and health_ok:
        return True, report, backup_name, file_count

    return _auto_rollback("فشلت إعادة التشغيل أو فحص الحالة", report, file_count)


# ── Telegram handlers ─────────────────────────────────────────────────────────

async def srv_update_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    await query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 رفع تحديث ZIP", callback_data="srv_update_zip_start")],
        [InlineKeyboardButton("🌿 تثبيت آخر إصدار من GitHub", callback_data="srv_deploy")],
        [InlineKeyboardButton("📦 سجل آخر التحديثات", callback_data="srv_update_history")],
        [InlineKeyboardButton("↩️ استرجاع آخر نسخة", callback_data="srv_rollback")],
        [InlineKeyboardButton("⬅ رجوع", callback_data="srv_menu")],
    ])
    await reply(update, "🚀 *إدارة التحديثات*\n\nاختر عملية:", kb)
    return SRV_UPDATE_MENU


async def srv_update_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    await query.answer()
    entries = get_recent_log_entries(matching_titles=(UPDATE_ZIP_TITLE, GITHUB_DEPLOY_TITLE), limit=10)
    if not entries:
        text = "📦 *سجل آخر التحديثات*\n\nلا يوجد أي تحديث مسجَّل بعد."
    else:
        text = "📦 *سجل آخر التحديثات* (آخر 10)\n\n" + "\n\n".join(entries)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="srv_update_menu")]])
    if len(text) > MAX_MESSAGE_LEN:
        await query.message.reply_text("📦 السجل طويل جداً — سيتم إرساله كملف نصي.")
        tmp_path = f"/tmp/update_history-{timestamp()}.txt"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(text)
            with open(tmp_path, "rb") as f:
                await query.message.reply_document(document=f, filename=os.path.basename(tmp_path),
                                                    caption="📦 سجل آخر التحديثات", reply_markup=kb)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    else:
        await reply(update, text, kb)
    return SRV_UPDATE_MENU


async def srv_update_zip_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    await query.answer()
    await reply(
        update,
        "📤 أرسل الآن ملف *ZIP* الخاص بالتحديث كمستند (Document) — يجب أن يكون امتداده `.zip`.",
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="srv_update_menu")]]),
    )
    return SRV_UPDATE_AWAIT_ZIP


def _cleanup_pending_zip(context: ContextTypes.DEFAULT_TYPE):
    session_dir = context.user_data.pop("srv_zip_session_dir", None)
    context.user_data.pop("srv_zip_path", None)
    context.user_data.pop("srv_zip_name", None)
    context.user_data.pop("srv_zip_size", None)
    context.user_data.pop("srv_zip_count", None)
    if session_dir:
        shutil.rmtree(session_dir, ignore_errors=True)


def _zip_review_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تثبيت", callback_data="srv_zip_install")],
        [InlineKeyboardButton("📋 عرض المحتويات", callback_data="srv_zip_list")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="srv_zip_cancel")],
    ])


async def srv_update_zip_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fires on any Document sent while awaiting a ZIP. Rejects anything
    that isn't a real .zip document; otherwise downloads it, inspects it,
    and shows the ✅ تثبيت / 📋 عرض المحتويات / ❌ إلغاء panel."""
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(NO_PERMISSION_TEXT)
        return ConversationHandler.END

    doc = update.message.document
    if not doc or not doc.file_name or not doc.file_name.lower().endswith(".zip"):
        await update.message.reply_text("❌ يجب إرسال ملف بامتداد .zip فقط كمستند (Document).")
        return SRV_UPDATE_AWAIT_ZIP

    lock = get_lock()
    if lock.locked():
        await update.message.reply_text("⏳ توجد عملية أخرى قيد التنفيذ، الرجاء الانتظار.")
        return SRV_UPDATE_AWAIT_ZIP

    os.makedirs(TMP_BASE, exist_ok=True)
    session_dir = os.path.join(TMP_BASE, timestamp())
    os.makedirs(session_dir, exist_ok=True)
    zip_path = os.path.join(session_dir, doc.file_name)

    try:
        file_obj = await context.bot.get_file(doc.file_id)
        await file_obj.download_to_drive(zip_path)
    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        await update.message.reply_text(f"⚠️ تعذر تحميل الملف:\n{e}")
        return SRV_UPDATE_AWAIT_ZIP

    if not zipfile.is_zipfile(zip_path):
        shutil.rmtree(session_dir, ignore_errors=True)
        await update.message.reply_text("❌ الملف المرسل ليس أرشيف ZIP صالحاً.")
        return SRV_UPDATE_AWAIT_ZIP

    try:
        with zipfile.ZipFile(zip_path) as zf:
            entry_count = len(zf.namelist())
    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        await update.message.reply_text(f"⚠️ تعذر قراءة الأرشيف:\n{e}")
        return SRV_UPDATE_AWAIT_ZIP

    size = os.path.getsize(zip_path)
    context.user_data["srv_zip_path"] = zip_path
    context.user_data["srv_zip_session_dir"] = session_dir
    context.user_data["srv_zip_name"] = doc.file_name
    context.user_data["srv_zip_size"] = size
    context.user_data["srv_zip_count"] = entry_count

    text = (
        f"📤 *تم استلام الملف*\n\n"
        f"📄 اسم الملف: {doc.file_name}\n"
        f"📦 الحجم: {fmt_size(size)}\n"
        f"🔢 عدد الملفات داخل الأرشيف: {entry_count}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=_zip_review_kb())
    return SRV_UPDATE_ZIP_REVIEW


async def srv_zip_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    await query.answer()
    zip_path = context.user_data.get("srv_zip_path")
    if not zip_path or not os.path.exists(zip_path):
        _cleanup_pending_zip(context)
        await reply(update, "⚠️ لم يعد الملف موجوداً. أرسل الملف مجدداً من القائمة.",
                    InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="srv_update_menu")]]))
        return SRV_UPDATE_MENU

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    text = "📋 *محتويات الأرشيف*\n\n" + "\n".join(names)

    if len(text) > MAX_MESSAGE_LEN:
        tmp_path = f"/tmp/zip_contents-{timestamp()}.txt"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(text)
            with open(tmp_path, "rb") as f:
                await query.message.reply_document(document=f, filename=os.path.basename(tmp_path),
                                                    caption="📋 محتويات الأرشيف")
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        await query.message.reply_text("اختر ما تريد فعله بالملف:", reply_markup=_zip_review_kb())
    else:
        await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=_zip_review_kb())
    return SRV_UPDATE_ZIP_REVIEW


async def srv_zip_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    await query.answer("تم الإلغاء وحذف الملف المؤقت.")
    _cleanup_pending_zip(context)
    return await srv_update_menu(update, context)


async def srv_zip_install(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU

    lock = get_lock()
    if lock.locked():
        await query.answer("⏳ توجد عملية أخرى قيد التنفيذ، الرجاء الانتظار.", show_alert=True)
        return SRV_UPDATE_ZIP_REVIEW

    zip_path = context.user_data.get("srv_zip_path")
    session_dir = context.user_data.get("srv_zip_session_dir")
    zip_name = context.user_data.get("srv_zip_name", "update.zip")
    zip_size = context.user_data.get("srv_zip_size", 0)
    zip_count = context.user_data.get("srv_zip_count", 0)

    if not zip_path or not os.path.exists(zip_path):
        await query.answer()
        _cleanup_pending_zip(context)
        await reply(update, "⚠️ لم يعد الملف موجوداً. أرسل الملف مجدداً من القائمة.",
                    InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="srv_update_menu")]]))
        return SRV_UPDATE_MENU

    await query.answer()
    progress = await query.message.reply_text(f"⌛ جاري التنفيذ...\n\n{UPDATE_ZIP_TITLE}")

    async with lock:
        start = time.monotonic()
        try:
            success, report_text, backup_name, file_count = await asyncio.to_thread(
                _run_zip_install_pipeline, zip_path, session_dir
            )
        except Exception as e:
            success, report_text, backup_name, file_count = False, f"⚠️ خطأ غير متوقع:\n{e}", None, 0
        duration = time.monotonic() - start

        log_action(
            update, UPDATE_ZIP_TITLE, duration, success,
            file=zip_name, size=fmt_size(zip_size), entries=zip_count,
        )

    _cleanup_pending_zip(context)

    result_line = "✅ تم تثبيت التحديث بنجاح." if success else "❌ فشل تثبيت التحديث."
    header = (
        f"{result_line}\n\n"
        f"⏱ مدة التنفيذ: {duration:.1f} ثانية\n"
        f"📦 عدد الملفات: {file_count}\n"
    )
    if backup_name:
        header += f"💾 النسخة الاحتياطية: {backup_name}\n"
    header += "\n"
    full_text = header + report_text

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للوحة السيرفر", callback_data="srv_menu")]])
    if len(full_text) > MAX_MESSAGE_LEN:
        tmp_path = f"/tmp/zip_install_result-{timestamp()}.txt"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(full_text)
            with open(tmp_path, "rb") as f:
                await progress.reply_document(document=f, filename=os.path.basename(tmp_path), caption=header)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        try:
            await progress.edit_text(header + "تم إرسال التفاصيل الكاملة كملف نصي.", reply_markup=kb)
        except Exception:
            pass
    else:
        try:
            await progress.edit_text(full_text, reply_markup=kb)
        except Exception:
            await progress.reply_text(full_text, reply_markup=kb)
    return SRV_MENU
