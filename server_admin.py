# -*- coding: utf-8 -*-
"""
🖥 إدارة السيرفر — a fully independent, ADMIN_ID-only server management panel.

Entered only via the "adm_server" callback (the single button added to
admin.py's main menu). All 13 actions run real Linux commands through
subprocess.run() (never os.system), each with an explicit timeout, and every
result is sent back into the chat — as a plain message, or automatically as
a .txt file if it would exceed ~3500 characters.

This file owns ALL server-management logic and state; admin.py/main.py only
need to import build_server_admin_handler() and register it, and add the
"🖥 إدارة السيرفر" button to the existing admin menu — nothing else about
this feature lives anywhere else.
"""
import io
import logging
import os
import subprocess
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler
from telegram.constants import ParseMode

from config import ADMIN_ID

logger = logging.getLogger(__name__)

# ── States ────────────────────────────────────────────────────────────────────
SRV_MENU, SRV_CONFIRM = range(2)

# ── Server-specific paths/names (adjust here only, nowhere else) ────────────
BOT_DIR         = "/root/alb70bot"
SERVICE_NAME    = "alb70bot"
BACKUPS_DIR     = "/root/backups"
DEPLOY_SCRIPT   = "/root/deploy.sh"
ROLLBACK_SCRIPT = "/root/rollback.sh"
KEEP_BACKUPS    = 10

MAX_MESSAGE_LEN = 3500


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


def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 نشر آخر تحديث", callback_data="srv_deploy")],
        [InlineKeyboardButton("↩️ استرجاع آخر نسخة", callback_data="srv_rollback")],
        [InlineKeyboardButton("🔄 إعادة تشغيل البوت", callback_data="srv_restart")],
        [InlineKeyboardButton("⛔ إيقاف الخدمة", callback_data="srv_stop")],
        [InlineKeyboardButton("▶ تشغيل الخدمة", callback_data="srv_start")],
        [InlineKeyboardButton("📊 حالة البوت", callback_data="srv_status")],
        [InlineKeyboardButton("📜 آخر 50 سجل", callback_data="srv_logs")],
        [InlineKeyboardButton("📜 آخر الأخطاء", callback_data="srv_errors")],
        [InlineKeyboardButton("💾 إنشاء نسخة احتياطية", callback_data="srv_backup")],
        [InlineKeyboardButton("📂 عرض النسخ الاحتياطية", callback_data="srv_list_backups")],
        [InlineKeyboardButton("🧹 حذف النسخ القديمة", callback_data="srv_cleanup")],
        [InlineKeyboardButton("📦 تحديث المكتبات", callback_data="srv_pip_update")],
        [InlineKeyboardButton("🖥 معلومات السيرفر", callback_data="srv_sysinfo")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="adm_main")],
    ])


# ── Command execution helpers ────────────────────────────────────────────────

def _run(cmd, timeout: int, shell: bool = False, cwd: str = None) -> str:
    """Runs a real Linux command via subprocess.run() (never os.system),
    always with capture_output, text mode, and an explicit timeout. Returns
    a human-readable combined stdout/stderr/exit-code report — never raises."""
    try:
        result = subprocess.run(
            cmd, shell=shell, cwd=cwd,
            capture_output=True, text=True, timeout=timeout,
        )
        parts = []
        if result.stdout:
            parts.append(result.stdout.strip())
        if result.stderr:
            parts.append("── stderr ──\n" + result.stderr.strip())
        parts.append(f"(exit code: {result.returncode})")
        return "\n\n".join(p for p in parts if p)
    except subprocess.TimeoutExpired:
        return "⚠️ انتهى الوقت المحدد لتنفيذ الأمر (Timeout)."
    except FileNotFoundError as e:
        return f"⚠️ لم يتم العثور على الأمر أو الملف المطلوب:\n{e}"
    except Exception as e:
        return f"⚠️ حدث خطأ غير متوقع أثناء التنفيذ:\n{e}"


def act_deploy() -> str:
    return _run([DEPLOY_SCRIPT], timeout=180)


def act_rollback() -> str:
    return _run([ROLLBACK_SCRIPT], timeout=180)


def act_restart() -> str:
    return _run(["systemctl", "restart", SERVICE_NAME], timeout=30)


def act_stop() -> str:
    return _run(["systemctl", "stop", SERVICE_NAME], timeout=30)


def act_start() -> str:
    return _run(["systemctl", "start", SERVICE_NAME], timeout=30)


def act_status() -> str:
    return _run(["systemctl", "status", SERVICE_NAME, "--no-pager"], timeout=15)


def act_logs() -> str:
    return _run(["journalctl", "-u", SERVICE_NAME, "-n", "50", "--no-pager"], timeout=15)


def act_errors() -> str:
    return _run(["journalctl", "-u", SERVICE_NAME, "-p", "err", "-n", "50", "--no-pager"], timeout=15)


def act_backup() -> str:
    try:
        os.makedirs(BACKUPS_DIR, exist_ok=True)
    except Exception as e:
        return f"⚠️ تعذر إنشاء مجلد النسخ الاحتياطية:\n{e}"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_path = os.path.join(BACKUPS_DIR, f"manual-{ts}.tar.gz")
    return _run(["tar", "-czf", archive_path, BOT_DIR], timeout=180)


def act_list_backups() -> str:
    return _run(["ls", "-lh", BACKUPS_DIR], timeout=15)


def act_cleanup() -> str:
    """Deletes every backup file except the most recent KEEP_BACKUPS ones —
    implemented directly in Python (not a shell pipeline) for safety and
    predictability."""
    try:
        if not os.path.isdir(BACKUPS_DIR):
            return "📂 لا يوجد مجلد نسخ احتياطية بعد — لا شيء لتنظيفه."
        files = [os.path.join(BACKUPS_DIR, f) for f in os.listdir(BACKUPS_DIR)]
        files = [f for f in files if os.path.isfile(f)]
        files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
        to_delete = files[KEEP_BACKUPS:]
        if not to_delete:
            return f"✅ لا توجد نسخ زائدة عن آخر {KEEP_BACKUPS} نسخ. لم يُحذف شيء."
        deleted, failed = [], []
        for f in to_delete:
            try:
                os.remove(f)
                deleted.append(os.path.basename(f))
            except Exception as e:
                failed.append(f"{os.path.basename(f)} ({e})")
        lines = [f"🧹 تم حذف {len(deleted)} نسخة قديمة:"]
        lines.extend(f"  • {name}" for name in deleted)
        if failed:
            lines.append("\n⚠️ فشل حذف:")
            lines.extend(f"  • {name}" for name in failed)
        return "\n".join(lines)
    except Exception as e:
        return f"⚠️ حدث خطأ غير متوقع أثناء التنظيف:\n{e}"


def act_pip_update() -> str:
    # Needs an actual shell (source + &&), so this is the one action that
    # uses shell=True — still via subprocess.run(), never os.system().
    cmd = f"cd {BOT_DIR} && source venv/bin/activate && pip install -r requirements.txt"
    return _run(cmd, timeout=300, shell=True)


def act_sysinfo() -> str:
    checks = [
        ("hostname", ["hostname"]),
        ("uptime", ["uptime"]),
        ("free -h", ["free", "-h"]),
        ("df -h", ["df", "-h"]),
        ("python3 --version", ["python3", "--version"]),
    ]
    blocks = [f"── {label} ──\n{_run(cmd, timeout=10)}" for label, cmd in checks]
    return "\n\n".join(blocks)


ACTION_FUNCS = {
    "srv_deploy":       act_deploy,
    "srv_rollback":     act_rollback,
    "srv_restart":      act_restart,
    "srv_stop":         act_stop,
    "srv_start":        act_start,
    "srv_status":       act_status,
    "srv_logs":         act_logs,
    "srv_errors":       act_errors,
    "srv_backup":       act_backup,
    "srv_list_backups": act_list_backups,
    "srv_cleanup":      act_cleanup,
    "srv_pip_update":   act_pip_update,
    "srv_sysinfo":      act_sysinfo,
}

ACTION_TITLES = {
    "srv_deploy":       "🚀 نشر آخر تحديث",
    "srv_rollback":     "↩️ استرجاع آخر نسخة",
    "srv_restart":      "🔄 إعادة تشغيل البوت",
    "srv_stop":         "⛔ إيقاف الخدمة",
    "srv_start":        "▶ تشغيل الخدمة",
    "srv_status":       "📊 حالة البوت",
    "srv_logs":         "📜 آخر 50 سجل",
    "srv_errors":       "📜 آخر الأخطاء",
    "srv_backup":       "💾 إنشاء نسخة احتياطية",
    "srv_list_backups": "📂 عرض النسخ الاحتياطية",
    "srv_cleanup":      "🧹 حذف النسخ القديمة",
    "srv_pip_update":   "📦 تحديث المكتبات",
    "srv_sysinfo":      "🖥 معلومات السيرفر",
}

# Actions disruptive/risky enough to require an explicit "✅ نعم" confirmation
# first (not requested explicitly, but consistent with how every other
# destructive action in this bot already works, and cheap extra safety for
# commands that can restart/stop the bot or delete/change real files).
CONFIRM_REQUIRED = {"srv_deploy", "srv_rollback", "srv_restart", "srv_stop", "srv_cleanup", "srv_pip_update"}


async def _send_result(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, text: str):
    """Sends the command's result as a NEW message (so a running log of
    executed actions stays visible), automatically as a .txt file instead
    if it's too long for a single Telegram message."""
    body = text or "(لا يوجد ناتج)"
    full = f"{title}\n\n{body}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للوحة السيرفر", callback_data="srv_menu")]])
    message = update.callback_query.message if update.callback_query else update.message

    if len(full) > MAX_MESSAGE_LEN:
        file_bytes = io.BytesIO(full.encode("utf-8"))
        safe_name = "".join(c for c in title if c.isalnum()) or "result"
        file_bytes.name = f"{safe_name}.txt"
        try:
            await message.reply_document(document=file_bytes, caption=title)
        except Exception as e:
            logger.error("server_admin: failed to send result as file: %s", e)
        await message.reply_text("اكتملت العملية. النتيجة أعلاه كملف نصي لتجاوزها الحد المسموح به.", reply_markup=kb)
    else:
        await message.reply_text(full, reply_markup=kb)


# ── Handlers ──────────────────────────────────────────────────────────────────

async def srv_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else None
    if update.callback_query:
        await update.callback_query.answer()
    if not _is_owner(user_id):
        if update.callback_query:
            await update.callback_query.answer("⛔ ليس لديك صلاحية.", show_alert=True)
        else:
            await update.message.reply_text("⛔ ليس لديك صلاحية.")
        return ConversationHandler.END
    await _reply(update, "🖥 *إدارة السيرفر*\n\nاختر عملية:", _menu_kb())
    return SRV_MENU


async def srv_menu_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update.effective_user.id):
        await update.callback_query.answer("⛔ ليس لديك صلاحية.", show_alert=True)
        return ConversationHandler.END
    await update.callback_query.answer()
    await _reply(update, "🖥 *إدارة السيرفر*\n\nاختر عملية:", _menu_kb())
    return SRV_MENU


async def srv_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_owner(query.from_user.id):
        await query.answer("⛔ ليس لديك صلاحية.", show_alert=True)
        return SRV_MENU

    action = query.data
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

    await query.answer("⏳ جارٍ التنفيذ...")
    func = ACTION_FUNCS.get(action)
    if not func:
        return SRV_MENU
    result_text = func()
    await _send_result(update, context, ACTION_TITLES.get(action, action), result_text)
    return SRV_MENU


async def srv_confirm_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_owner(query.from_user.id):
        await query.answer("⛔ ليس لديك صلاحية.", show_alert=True)
        return SRV_MENU

    action = "srv_" + query.data.replace("srv_yes_", "", 1)
    await query.answer("⏳ جارٍ التنفيذ...")
    func = ACTION_FUNCS.get(action)
    if not func:
        return SRV_MENU
    result_text = func()
    await _send_result(update, context, ACTION_TITLES.get(action, action), result_text)
    return SRV_MENU


# ── Build the ConversationHandler ─────────────────────────────────────────────

def build_server_admin_handler() -> ConversationHandler:
    action_pattern = r"^srv_(deploy|rollback|restart|stop|start|status|logs|errors|backup|list_backups|cleanup|pip_update|sysinfo)$"
    confirm_pattern = r"^srv_yes_(deploy|rollback|restart|stop|cleanup|pip_update)$"

    return ConversationHandler(
        entry_points=[CallbackQueryHandler(srv_hub, pattern="^adm_server$")],
        states={
            SRV_MENU: [
                CallbackQueryHandler(srv_menu_back, pattern="^srv_menu$"),
                CallbackQueryHandler(srv_button, pattern=action_pattern),
            ],
            SRV_CONFIRM: [
                CallbackQueryHandler(srv_menu_back, pattern="^srv_menu$"),
                CallbackQueryHandler(srv_confirm_yes, pattern=confirm_pattern),
            ],
        },
        fallbacks=[CallbackQueryHandler(srv_hub, pattern="^adm_server$")],
        name="server_admin_conv",
        persistent=False,
        allow_reentry=True,
    )
