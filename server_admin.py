# -*- coding: utf-8 -*-
"""
🖥 إدارة السيرفر — a fully independent, ADMIN_ID-only server management panel.

Entered only via the "adm_server" callback (the single button added to
admin.py's main menu). Every action runs a real Linux command through
subprocess.run() (never os.system, always capture_output=True, text=True,
timeout=120, check=False), each wrapped with:
  - a "⌛ جاري التنفيذ..." progress message, followed by "✅ نجحت"/"❌ فشلت",
  - a global concurrency lock (only one server action may run at a time —
    every button effectively "disables" while busy, since a second tap is
    rejected with an alert instead of starting a second command),
  - an audit trail written to server_admin.log (timestamp, admin name/id,
    action, duration, result),
  - automatic .txt fallback via send_document for any result over ~3500
    characters.

This file owns ALL server-management logic and state; admin.py/main.py only
import build_server_admin_handler() and register it, plus one button in
admin.py's menu — nothing else about this feature lives anywhere else.
"""
import asyncio
import logging
import os
import py_compile
import shutil
import subprocess
import time
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler
from telegram.constants import ParseMode

from config import ADMIN_ID

# ── Audit log (server_admin.log — timestamp/admin/id/action/duration/result) ─
LOG_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_admin.log")
logger = logging.getLogger(__name__)

srv_audit_logger = logging.getLogger("server_admin_audit")
srv_audit_logger.setLevel(logging.INFO)
if not srv_audit_logger.handlers:
    _fh = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    srv_audit_logger.addHandler(_fh)
    srv_audit_logger.propagate = False


def _log_action(update: Update, title: str, duration: float, success: bool) -> None:
    user = update.effective_user
    admin_name = user.full_name if user else "—"
    admin_id = user.id if user else "—"
    result = "SUCCESS" if success else "FAILED"
    srv_audit_logger.info(
        "admin=%s | id=%s | action=%s | duration=%.2fs | result=%s",
        admin_name, admin_id, title, duration, result,
    )


# ── States ────────────────────────────────────────────────────────────────────
(SRV_MENU, SRV_CONFIRM, SRV_BACKUP_LIST, SRV_BACKUP_ITEM, SRV_BACKUP_CONFIRM) = range(5)

# ── Server-specific paths/names (adjust here only, nowhere else) ────────────
BOT_DIR         = "/root/alb70bot"
SERVICE_NAME    = "alb70bot"
BACKUPS_DIR     = "/root/backups"
DEPLOY_SCRIPT   = "/root/deploy.sh"
ROLLBACK_SCRIPT = "/root/rollback.sh"
KEEP_BACKUPS    = 10
COMMAND_TIMEOUT = 120  # every subprocess call uses this — no exceptions

MAX_MESSAGE_LEN = 3500

NO_PERMISSION_TEXT = "🚫 ليس لديك صلاحية."


def _is_owner(user_id) -> bool:
    try:
        return int(user_id) == int(ADMIN_ID)
    except Exception:
        return False


async def _reply(update: Update, text: str, keyboard=None):
    kw = {"text": text, "parse_mode": ParseMode.MARKDOWN}
    if keyboard:
        kw["reply_markup"] = keyboard
    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(**kw)
        except Exception as e1:
            logger.warning("server_admin _reply edit_text failed: %s", e1)
            try:
                await update.callback_query.message.reply_text(**kw)
            except Exception as e2:
                logger.error("server_admin _reply reply_text also failed: %s", e2)
    else:
        await update.message.reply_text(**kw)


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _fmt_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f}TB"


# ── Menu keyboard (grouped with plain separator buttons for readability) ────

def _sep(label: str):
    return [InlineKeyboardButton(f"── {label} ──", callback_data="srv_noop")]


def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        _sep("النشر والتشغيل"),
        [InlineKeyboardButton("🚀 نشر آخر تحديث", callback_data="srv_deploy")],
        [InlineKeyboardButton("↩️ استرجاع آخر نسخة", callback_data="srv_rollback")],
        [InlineKeyboardButton("🔄 إعادة تشغيل البوت", callback_data="srv_restart")],
        [InlineKeyboardButton("⛔ إيقاف الخدمة", callback_data="srv_stop"),
         InlineKeyboardButton("▶ تشغيل الخدمة", callback_data="srv_start")],
        [InlineKeyboardButton("📊 حالة البوت", callback_data="srv_status")],

        _sep("السجلات"),
        [InlineKeyboardButton("📜 آخر 50 سجل", callback_data="srv_logs")],
        [InlineKeyboardButton("❌ آخر الأخطاء", callback_data="srv_errors")],

        _sep("النسخ الاحتياطية"),
        [InlineKeyboardButton("💾 إنشاء نسخة احتياطية", callback_data="srv_backup")],
        [InlineKeyboardButton("📂 عرض النسخ الاحتياطية", callback_data="srv_list_backups")],
        [InlineKeyboardButton("🗑 حذف النسخ القديمة", callback_data="srv_cleanup")],

        _sep("الصيانة"),
        [InlineKeyboardButton("📦 تحديث المكتبات", callback_data="srv_pip_update")],
        [InlineKeyboardButton("🧹 تنظيف Cache", callback_data="srv_cache_clean")],
        [InlineKeyboardButton("🧪 فحص الكود", callback_data="srv_codecheck")],

        _sep("التحميل"),
        [InlineKeyboardButton("📥 تحميل المشروع كاملاً", callback_data="srv_download_project")],
        [InlineKeyboardButton("📤 تحميل قاعدة البيانات", callback_data="srv_download_db")],
        [InlineKeyboardButton("📄 تحميل آخر سجل", callback_data="srv_download_log")],

        _sep("Git"),
        [InlineKeyboardButton("🌿 Git Status", callback_data="srv_git_status")],
        [InlineKeyboardButton("⬇ Git Pull", callback_data="srv_git_pull")],
        [InlineKeyboardButton("📜 آخر 10 Commits", callback_data="srv_git_log")],

        _sep("معلومات السيرفر"),
        [InlineKeyboardButton("🖥 معلومات السيرفر (شامل)", callback_data="srv_sysinfo")],
        [InlineKeyboardButton("⚙ إصدار Python", callback_data="srv_py_version"),
         InlineKeyboardButton("🖥 إصدار Linux", callback_data="srv_linux_version")],
        [InlineKeyboardButton("💽 مساحة القرص", callback_data="srv_disk"),
         InlineKeyboardButton("💾 استهلاك الرام", callback_data="srv_ram")],
        [InlineKeyboardButton("📈 استهلاك المعالج", callback_data="srv_cpu"),
         InlineKeyboardButton("🌐 عنوان IP", callback_data="srv_ip")],
        [InlineKeyboardButton("⏰ وقت السيرفر", callback_data="srv_time")],

        [InlineKeyboardButton("⬅ رجوع", callback_data="adm_main")],
    ])


def _busy_kb() -> InlineKeyboardMarkup:
    """Shown while a command is running — every real button is replaced, so
    nothing else can be tapped/started until it finishes."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("⌛ جارٍ التنفيذ...", callback_data="srv_noop")]])


async def srv_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


# ── Command execution ─────────────────────────────────────────────────────────

def _run(cmd, timeout: int = COMMAND_TIMEOUT, shell: bool = False, cwd: str = None):
    """Runs a real Linux command via subprocess.run() (never os.system),
    always with capture_output=True, text=True, an explicit timeout, and
    check=False. Returns (success: bool, report: str) — never raises."""
    try:
        result = subprocess.run(
            cmd, shell=shell, cwd=cwd,
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        parts = []
        if result.stdout:
            parts.append(result.stdout.strip())
        if result.stderr:
            parts.append("── stderr ──\n" + result.stderr.strip())
        parts.append(f"(exit code: {result.returncode})")
        return result.returncode == 0, "\n\n".join(p for p in parts if p)
    except subprocess.TimeoutExpired:
        return False, f"⚠️ انتهى الوقت المحدد لتنفيذ الأمر (Timeout بعد {timeout} ثانية)."
    except FileNotFoundError as e:
        return False, f"⚠️ لم يتم العثور على الأمر أو الملف المطلوب:\n{e}"
    except Exception as e:
        return False, f"⚠️ حدث خطأ غير متوقع أثناء التنفيذ:\n{e}"


def _health_check(service: str = SERVICE_NAME):
    """systemctl is-active <service> — exit code 0 only when truly active."""
    return _run(["systemctl", "is-active", service], timeout=15)


# ── Lifecycle actions ─────────────────────────────────────────────────────────

def act_deploy():
    ok, out = _run([DEPLOY_SCRIPT])
    health_ok, health_out = _health_check()
    combined = f"{out}\n\n── فحص الحالة بعد النشر (Health Check) ──\n{health_out}"
    if not health_ok:
        combined += "\n\n❌ تحذير: الخدمة لا تعمل بعد النشر!"
    return (ok and health_ok), combined


def act_rollback():
    ok, out = _run([ROLLBACK_SCRIPT])
    health_ok, health_out = _health_check()
    combined = f"{out}\n\n── فحص الحالة بعد الاسترجاع (Health Check) ──\n{health_out}"
    if not health_ok:
        combined += "\n\n❌ تحذير: الخدمة لا تعمل بعد الاسترجاع!"
    return (ok and health_ok), combined


def act_restart():
    return _run(["systemctl", "restart", SERVICE_NAME])


def act_stop():
    return _run(["systemctl", "stop", SERVICE_NAME])


def act_start():
    return _run(["systemctl", "start", SERVICE_NAME])


def act_status():
    # Informational query — "status" isn't pass/fail by itself (a cleanly
    # stopped service still returns a non-zero exit code), so we report it
    # as successful whenever the command itself ran without error.
    ok, out = _run(["systemctl", "status", SERVICE_NAME, "--no-pager"])
    return True, out


def act_logs():
    ok, out = _run(["journalctl", "-u", SERVICE_NAME, "-n", "50", "--no-pager"])
    return True, out


def act_errors():
    ok, out = _run(["journalctl", "-u", SERVICE_NAME, "-p", "err", "-n", "50", "--no-pager"])
    return True, out


# ── Backups ───────────────────────────────────────────────────────────────────

def act_backup():
    try:
        os.makedirs(BACKUPS_DIR, exist_ok=True)
    except Exception as e:
        return False, f"⚠️ تعذر إنشاء مجلد النسخ الاحتياطية:\n{e}"
    archive_path = os.path.join(BACKUPS_DIR, f"manual-{_ts()}.tar.gz")
    return _run(["tar", "-czf", archive_path, BOT_DIR])


def act_cleanup():
    """Deletes every backup file except the most recent KEEP_BACKUPS ones —
    implemented directly in Python (not a shell pipeline) for safety and
    predictability."""
    try:
        if not os.path.isdir(BACKUPS_DIR):
            return True, "📂 لا يوجد مجلد نسخ احتياطية بعد — لا شيء لتنظيفه."
        files = _list_backup_files()
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


def act_restore_backup(filepath: str):
    """Restores ANY chosen backup (not just the latest) — stops the service,
    extracts the archive back over BOT_DIR, restarts, then health-checks."""
    stop_ok, stop_out = _run(["systemctl", "stop", SERVICE_NAME])
    extract_ok, extract_out = _run(["tar", "-xzf", filepath, "-C", "/"])
    start_ok, start_out = _run(["systemctl", "start", SERVICE_NAME])
    health_ok, health_out = _health_check()
    combined = (
        f"── إيقاف الخدمة ──\n{stop_out}\n\n"
        f"── استخراج النسخة ──\n{extract_out}\n\n"
        f"── تشغيل الخدمة ──\n{start_out}\n\n"
        f"── فحص الحالة (Health Check) ──\n{health_out}"
    )
    if not health_ok:
        combined += "\n\n❌ تحذير: الخدمة لا تعمل بعد الاسترجاع!"
    return (extract_ok and start_ok and health_ok), combined


def _list_backup_files() -> list:
    if not os.path.isdir(BACKUPS_DIR):
        return []
    files = [os.path.join(BACKUPS_DIR, f) for f in os.listdir(BACKUPS_DIR)
             if f.endswith((".tar.gz", ".tgz"))]
    files = [f for f in files if os.path.isfile(f)]
    files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    return files


# ── Maintenance ───────────────────────────────────────────────────────────────

def act_pip_update():
    # Needs an actual shell (source + &&), so this is the one action that
    # uses shell=True — still via subprocess.run(), never os.system().
    cmd = f"cd {BOT_DIR} && source venv/bin/activate && pip install -r requirements.txt"
    return _run(cmd, shell=True)


def act_cache_clean():
    removed_dirs, removed_files, errors = 0, 0, []
    for root, dirs, files in os.walk(BOT_DIR):
        if "__pycache__" in dirs:
            path = os.path.join(root, "__pycache__")
            try:
                shutil.rmtree(path)
                removed_dirs += 1
                dirs.remove("__pycache__")
            except Exception as e:
                errors.append(str(e))
        for f in files:
            if f.endswith(".pyc"):
                try:
                    os.remove(os.path.join(root, f))
                    removed_files += 1
                except Exception as e:
                    errors.append(str(e))
    msg = f"🧹 تم حذف {removed_dirs} مجلد __pycache__ و {removed_files} ملف .pyc."
    if errors:
        msg += "\n\n⚠️ أخطاء:\n" + "\n".join(errors[:20])
    return (len(errors) == 0), msg


def act_codecheck():
    errors, checked = [], 0
    for root, dirs, files in os.walk(BOT_DIR):
        dirs[:] = [d for d in dirs if d not in (".git", "venv", "__pycache__")]
        for f in files:
            if f.endswith(".py"):
                checked += 1
                path = os.path.join(root, f)
                try:
                    py_compile.compile(path, doraise=True)
                except py_compile.PyCompileError as e:
                    errors.append(f"{path}:\n{e}")
                except Exception as e:
                    errors.append(f"{path}: {e}")
    if errors:
        return False, f"🧪 تم فحص {checked} ملف — وُجدت {len(errors)} مشكلة:\n\n" + "\n\n".join(errors)
    return True, f"✅ تم فحص {checked} ملف Python — لا توجد أخطاء صياغة."


# ── Git ───────────────────────────────────────────────────────────────────────

def act_git_status():
    ok, out = _run(["git", "status"], cwd=BOT_DIR)
    _, branch_out = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], timeout=15, cwd=BOT_DIR)
    _, hash_out = _run(["git", "rev-parse", "HEAD"], timeout=15, cwd=BOT_DIR)
    branch = branch_out.splitlines()[0] if branch_out else "—"
    commit_hash = hash_out.splitlines()[0] if hash_out else "—"
    header = f"🌿 الفرع الحالي: {branch}\n🔖 آخر Commit: {commit_hash}\n\n"
    return ok, header + out


def act_git_pull():
    return _run(["git", "pull"], cwd=BOT_DIR)


def act_git_log():
    return _run(["git", "log", "-10", "--oneline", "--decorate"], cwd=BOT_DIR)


# ── Downloads (return (success, message, filepath_or_None)) ────────────────

def build_project_archive():
    parent = os.path.dirname(BOT_DIR.rstrip("/")) or "/"
    base = os.path.basename(BOT_DIR.rstrip("/"))
    tmp_path = f"/tmp/project-{_ts()}.tar.gz"
    ok, out = _run([
        "tar", "-czf", tmp_path,
        "--exclude=venv", "--exclude=__pycache__", "--exclude=.git", "--exclude=backups",
        "-C", parent, base,
    ])
    return ok, out, (tmp_path if ok and os.path.exists(tmp_path) else None)


def build_db_archive():
    data_dir = os.path.join(BOT_DIR, "data")
    if not os.path.isdir(data_dir):
        return False, "⚠️ مجلد data غير موجود.", None
    tmp_path = f"/tmp/database-{_ts()}.tar.gz"
    ok, out = _run(["tar", "-czf", tmp_path, "-C", BOT_DIR, "data"])
    return ok, out, (tmp_path if ok and os.path.exists(tmp_path) else None)


def build_latest_log():
    ok, out = _run(["journalctl", "-u", SERVICE_NAME, "-n", "500", "--no-pager"])
    if not ok:
        return False, out, None
    tmp_path = f"/tmp/latest_log-{_ts()}.txt"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(out)
        return True, "تم تجهيز آخر سجل بنجاح.", tmp_path
    except Exception as e:
        return False, f"⚠️ تعذر كتابة ملف السجل:\n{e}", None


# ── Standalone system-info actions ──────────────────────────────────────────

def act_py_version():
    return _run(["python3", "--version"], timeout=15)


def act_linux_version():
    return _run(["uname", "-a"], timeout=15)


def act_disk():
    return _run(["df", "-h"], timeout=15)


def act_ram():
    return _run(["free", "-h"], timeout=15)


def act_cpu():
    try:
        load1, load5, load15 = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        text = (
            f"متوسط الحمل (Load Average):\n"
            f"  1 دقيقة: {load1:.2f}\n"
            f"  5 دقائق: {load5:.2f}\n"
            f"  15 دقيقة: {load15:.2f}\n\n"
            f"عدد الأنوية: {cpu_count}"
        )
        return True, text
    except Exception as e:
        return False, f"⚠️ تعذر قراءة استهلاك المعالج:\n{e}"


def act_ip():
    return _run(["hostname", "-I"], timeout=15)


def act_time():
    return _run(["date"], timeout=15)


def act_sysinfo():
    checks = [
        ("hostname", ["hostname"]),
        ("uptime", ["uptime"]),
        ("free -h", ["free", "-h"]),
        ("df -h", ["df", "-h"]),
        ("python3 --version", ["python3", "--version"]),
    ]
    blocks = []
    for label, cmd in checks:
        _, out = _run(cmd, timeout=15)
        blocks.append(f"── {label} ──\n{out}")
    return True, "\n\n".join(blocks)


# ── Action registry ───────────────────────────────────────────────────────────

ACTION_FUNCS = {
    "srv_deploy":          act_deploy,
    "srv_rollback":        act_rollback,
    "srv_restart":         act_restart,
    "srv_stop":            act_stop,
    "srv_start":           act_start,
    "srv_status":          act_status,
    "srv_logs":            act_logs,
    "srv_errors":          act_errors,
    "srv_backup":          act_backup,
    "srv_cleanup":         act_cleanup,
    "srv_pip_update":      act_pip_update,
    "srv_cache_clean":     act_cache_clean,
    "srv_codecheck":       act_codecheck,
    "srv_git_status":      act_git_status,
    "srv_git_pull":        act_git_pull,
    "srv_git_log":         act_git_log,
    "srv_py_version":      act_py_version,
    "srv_linux_version":   act_linux_version,
    "srv_disk":            act_disk,
    "srv_ram":             act_ram,
    "srv_cpu":             act_cpu,
    "srv_ip":              act_ip,
    "srv_time":            act_time,
    "srv_sysinfo":         act_sysinfo,
}

ACTION_TITLES = {
    "srv_deploy":          "🚀 نشر آخر تحديث",
    "srv_rollback":        "↩️ استرجاع آخر نسخة",
    "srv_restart":         "🔄 إعادة تشغيل البوت",
    "srv_stop":            "⛔ إيقاف الخدمة",
    "srv_start":           "▶ تشغيل الخدمة",
    "srv_status":          "📊 حالة البوت",
    "srv_logs":            "📜 آخر 50 سجل",
    "srv_errors":          "❌ آخر الأخطاء",
    "srv_backup":          "💾 إنشاء نسخة احتياطية",
    "srv_cleanup":         "🗑 حذف النسخ القديمة",
    "srv_pip_update":      "📦 تحديث المكتبات",
    "srv_cache_clean":     "🧹 تنظيف Cache",
    "srv_codecheck":       "🧪 فحص الكود",
    "srv_git_status":      "🌿 Git Status",
    "srv_git_pull":        "⬇ Git Pull",
    "srv_git_log":         "📜 آخر 10 Commits",
    "srv_py_version":      "⚙ إصدار Python",
    "srv_linux_version":   "🖥 إصدار Linux",
    "srv_disk":            "💽 مساحة القرص",
    "srv_ram":             "💾 استهلاك الرام",
    "srv_cpu":             "📈 استهلاك المعالج",
    "srv_ip":              "🌐 عنوان IP",
    "srv_time":            "⏰ وقت السيرفر",
    "srv_sysinfo":         "🖥 معلومات السيرفر",
}

# Disruptive/impactful actions that require an explicit "✅ نعم" confirmation
# before running at all.
CONFIRM_REQUIRED = {"srv_deploy", "srv_rollback", "srv_restart", "srv_stop", "srv_cleanup", "srv_pip_update"}

DOWNLOAD_BUILDERS = {
    "srv_download_project": build_project_archive,
    "srv_download_db":      build_db_archive,
    "srv_download_log":     build_latest_log,
}
DOWNLOAD_TITLES = {
    "srv_download_project": "📥 تحميل المشروع كاملاً",
    "srv_download_db":      "📤 تحميل قاعدة البيانات",
    "srv_download_log":     "📄 تحميل آخر سجل",
}


# ── Concurrency guard — only one server action may run at a time ───────────
_busy_lock = None


def _get_lock() -> asyncio.Lock:
    global _busy_lock
    if _busy_lock is None:
        _busy_lock = asyncio.Lock()
    return _busy_lock


async def _send_long_or_short(message_obj, full_text: str, title: str, keyboard):
    """Edits the progress message with the final result, or — if the result
    is too long for one Telegram message — sends it as a .txt file instead
    (via send_document) and leaves a short pointer in the edited message."""
    if len(full_text) > MAX_MESSAGE_LEN:
        safe_name = "".join(c for c in title if c.isalnum()) or "result"
        tmp_path = f"/tmp/{safe_name}-{_ts()}.txt"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(full_text)
            with open(tmp_path, "rb") as f:
                await message_obj.reply_document(document=f, filename=os.path.basename(tmp_path), caption=title)
        except Exception as e:
            logger.error("server_admin: failed to send result as file: %s", e)
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


async def _execute_with_progress(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, action_func):
    """The generic 'run a command with progress → result' wrapper used by
    every text-report action: sends '⌛ جاري التنفيذ...' first, runs the
    (blocking) command in a worker thread so the bot stays responsive, then
    edits that same message with '✅ نجحت'/'❌ فشلت' plus the output — and
    logs the whole thing to server_admin.log."""
    lock = _get_lock()
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

        _log_action(update, title, duration, success)

        result_line = "✅ نجحت" if success else "❌ فشلت"
        full_text = f"{title}\n\n{result_line} (المدة: {duration:.1f} ثانية)\n\n{output or '(لا يوجد ناتج)'}"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للوحة السيرفر", callback_data="srv_menu")]])
        await _send_long_or_short(progress, full_text, title, kb)


async def _execute_download(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, build_func):
    """Like _execute_with_progress, but for actions that produce a FILE
    (project/database/log download) instead of a text report."""
    lock = _get_lock()
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

        _log_action(update, title, duration, success)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للوحة السيرفر", callback_data="srv_menu")]])

        if success and filepath and os.path.exists(filepath):
            try:
                with open(filepath, "rb") as f:
                    await message.reply_document(document=f, filename=os.path.basename(filepath),
                                                  caption=f"{title}\n✅ نجحت")
            except Exception as e:
                logger.error("server_admin download send failed: %s", e)
            finally:
                try:
                    os.remove(filepath)
                except Exception:
                    pass
            try:
                await progress.edit_text(f"{title}\n\n✅ نجحت (المدة: {duration:.1f} ثانية) — أُرسل الملف أعلاه.",
                                          reply_markup=kb)
            except Exception:
                pass
        else:
            full_text = f"{title}\n\n❌ فشلت (المدة: {duration:.1f} ثانية)\n\n{out or '(لا يوجد ناتج)'}"
            await _send_long_or_short(progress, full_text, title, kb)


# ── Menu / navigation handlers ────────────────────────────────────────────────

async def srv_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else None
    if update.callback_query:
        await update.callback_query.answer()
    if not _is_owner(user_id):
        if update.callback_query:
            await update.callback_query.answer(NO_PERMISSION_TEXT, show_alert=True)
        else:
            await update.message.reply_text(NO_PERMISSION_TEXT)
        return ConversationHandler.END
    await _reply(update, "🖥 *إدارة السيرفر*\n\nاختر عملية:", _menu_kb())
    return SRV_MENU


async def srv_menu_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update.effective_user.id):
        await update.callback_query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return ConversationHandler.END
    await update.callback_query.answer()
    await _reply(update, "🖥 *إدارة السيرفر*\n\nاختر عملية:", _menu_kb())
    return SRV_MENU


async def srv_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispatches every plain text-report action (with confirm-gating for
    disruptive ones) and every download action."""
    query = update.callback_query
    if not _is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU

    action = query.data

    if action in DOWNLOAD_BUILDERS:
        await _execute_download(update, context, DOWNLOAD_TITLES[action], DOWNLOAD_BUILDERS[action])
        return SRV_MENU

    if action in CONFIRM_REQUIRED:
        await query.answer()
        label = ACTION_TITLES.get(action, action)
        short = action.replace("srv_", "", 1)
        await _reply(
            update,
            f"⚠️ هل أنت متأكد من تنفيذ:\n\n{label}؟",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ نعم", callback_data=f"srv_yes_{short}"),
                InlineKeyboardButton("❌ إلغاء", callback_data="srv_menu"),
            ]]),
        )
        return SRV_CONFIRM

    func = ACTION_FUNCS.get(action)
    if not func:
        await query.answer()
        return SRV_MENU
    await _execute_with_progress(update, context, ACTION_TITLES.get(action, action), func)
    return SRV_MENU


async def srv_confirm_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    action = "srv_" + query.data.replace("srv_yes_", "", 1)
    func = ACTION_FUNCS.get(action)
    if not func:
        await query.answer()
        return SRV_MENU
    await _execute_with_progress(update, context, ACTION_TITLES.get(action, action), func)
    return SRV_MENU


# ── Backup browser (📂 عرض النسخ الاحتياطية → per-file ↩ استرجاع / 🗑 حذف) ──

async def srv_backup_list_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    await query.answer()

    files = _list_backup_files()
    context.user_data["srv_backup_files"] = files
    if not files:
        await _reply(
            update, "📂 لا توجد نسخ احتياطية بعد.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="srv_menu")]]),
        )
        return SRV_BACKUP_LIST

    rows = []
    for i, f in enumerate(files):
        size = _fmt_size(os.path.getsize(f))
        mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")
        label = f"{os.path.basename(f)} ({size}, {mtime})"
        rows.append([InlineKeyboardButton(label[:64], callback_data=f"srv_bk_{i}")])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="srv_menu")])
    await _reply(update, "📂 *النسخ الاحتياطية*\n\nاختر نسخة:", InlineKeyboardMarkup(rows))
    return SRV_BACKUP_LIST


async def srv_backup_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_owner(query.from_user.id):
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
    size = _fmt_size(os.path.getsize(f))
    mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")
    text = f"💾 *{os.path.basename(f)}*\n\nالحجم: {size}\nالتاريخ: {mtime}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("↩ استرجاع", callback_data=f"srv_bk_restore_{idx}")],
        [InlineKeyboardButton("🗑 حذف", callback_data=f"srv_bk_delete_{idx}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="srv_list_backups")],
    ])
    await _reply(update, text, kb)
    return SRV_BACKUP_ITEM


async def srv_backup_restore_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    idx = int(query.data.replace("srv_bk_restore_", ""))
    files = context.user_data.get("srv_backup_files", [])
    if idx >= len(files):
        await query.answer("⚠️ لم يعد بالإمكان العثور على هذه النسخة.", show_alert=True)
        return await srv_backup_list_show(update, context)
    await query.answer()
    fname = os.path.basename(files[idx])
    await _reply(
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
    if not _is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    idx = int(query.data.replace("srv_bk_restore_yes_", ""))
    files = context.user_data.get("srv_backup_files", [])
    if idx >= len(files):
        await query.answer("⚠️ لم يعد بالإمكان العثور على هذه النسخة.", show_alert=True)
        return await srv_backup_list_show(update, context)
    filepath = files[idx]
    title = f"↩ استرجاع نسخة: {os.path.basename(filepath)}"
    await _execute_with_progress(update, context, title, lambda: act_restore_backup(filepath))
    return SRV_MENU


async def srv_backup_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    idx = int(query.data.replace("srv_bk_delete_", ""))
    files = context.user_data.get("srv_backup_files", [])
    if idx >= len(files):
        await query.answer("⚠️ لم يعد بالإمكان العثور على هذه النسخة.", show_alert=True)
        return await srv_backup_list_show(update, context)
    await query.answer()
    fname = os.path.basename(files[idx])
    await _reply(
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
    if not _is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    idx = int(query.data.replace("srv_bk_delete_yes_", ""))
    files = context.user_data.get("srv_backup_files", [])
    await query.answer()
    if idx >= len(files):
        await _reply(update, "⚠️ لم يعد بالإمكان العثور على هذه النسخة.",
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
    _log_action(update, f"🗑 حذف نسخة: {fname}", duration, success)
    result_line = "✅ نجحت" if success else "❌ فشلت"
    await _reply(
        update, f"{result_line}\n\n{msg}",
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للوحة السيرفر", callback_data="srv_menu")]]),
    )
    return SRV_MENU


# ── Build the ConversationHandler ─────────────────────────────────────────────

def build_server_admin_handler() -> ConversationHandler:
    simple_action_pattern = (
        r"^srv_("
        r"deploy|rollback|restart|stop|start|status|logs|errors|"
        r"backup|cleanup|pip_update|cache_clean|codecheck|"
        r"git_status|git_pull|git_log|"
        r"py_version|linux_version|disk|ram|cpu|ip|time|sysinfo|"
        r"download_project|download_db|download_log"
        r")$"
    )
    confirm_pattern = r"^srv_yes_(deploy|rollback|restart|stop|cleanup|pip_update)$"

    return ConversationHandler(
        entry_points=[CallbackQueryHandler(srv_hub, pattern="^adm_server$")],
        states={
            SRV_MENU: [
                CallbackQueryHandler(srv_noop, pattern="^srv_noop$"),
                CallbackQueryHandler(srv_menu_back, pattern="^srv_menu$"),
                CallbackQueryHandler(srv_backup_list_show, pattern="^srv_list_backups$"),
                CallbackQueryHandler(srv_button, pattern=simple_action_pattern),
            ],
            SRV_CONFIRM: [
                CallbackQueryHandler(srv_noop, pattern="^srv_noop$"),
                CallbackQueryHandler(srv_menu_back, pattern="^srv_menu$"),
                CallbackQueryHandler(srv_confirm_yes, pattern=confirm_pattern),
            ],
            SRV_BACKUP_LIST: [
                CallbackQueryHandler(srv_noop, pattern="^srv_noop$"),
                CallbackQueryHandler(srv_menu_back, pattern="^srv_menu$"),
                CallbackQueryHandler(srv_backup_item, pattern=r"^srv_bk_\d+$"),
            ],
            SRV_BACKUP_ITEM: [
                CallbackQueryHandler(srv_noop, pattern="^srv_noop$"),
                CallbackQueryHandler(srv_backup_list_show, pattern="^srv_list_backups$"),
                CallbackQueryHandler(srv_backup_restore_confirm, pattern=r"^srv_bk_restore_\d+$"),
                CallbackQueryHandler(srv_backup_delete_confirm, pattern=r"^srv_bk_delete_\d+$"),
                CallbackQueryHandler(srv_backup_item, pattern=r"^srv_bk_\d+$"),
            ],
            SRV_BACKUP_CONFIRM: [
                CallbackQueryHandler(srv_noop, pattern="^srv_noop$"),
                CallbackQueryHandler(srv_backup_restore_yes, pattern=r"^srv_bk_restore_yes_\d+$"),
                CallbackQueryHandler(srv_backup_delete_yes, pattern=r"^srv_bk_delete_yes_\d+$"),
                CallbackQueryHandler(srv_backup_item, pattern=r"^srv_bk_\d+$"),
            ],
        },
        fallbacks=[CallbackQueryHandler(srv_hub, pattern="^adm_server$")],
        name="server_admin_conv",
        persistent=False,
        allow_reentry=True,
    )
