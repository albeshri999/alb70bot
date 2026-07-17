# -*- coding: utf-8 -*-
"""
server_admin.menu — the presentation/wiring layer (Clean Architecture's
"composition root" for this package): builds the keyboard, dispatches every
button to the right action function from the domain modules, enforces
confirmation for dangerous actions (rule #7), and assembles the single
ConversationHandler that main.py registers.

No business logic lives here — every action function comes from its own
domain module (deploy.py, rollback.py, update.py, monitor.py, logs.py,
system.py, github.py, backup.py); this module only wires callback_data to
those functions and renders the menu.
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler

from server_admin.utils import (
    is_owner, reply, NO_PERMISSION_TEXT,
    SRV_MENU, SRV_CONFIRM, SRV_BACKUP_LIST, SRV_BACKUP_ITEM, SRV_BACKUP_CONFIRM,
)
from server_admin.notifications import execute_with_progress, execute_download

from server_admin.deploy import act_deploy
from server_admin.rollback import act_rollback
from server_admin.update import act_pip_update, act_cache_clean, act_codecheck
from server_admin.monitor import act_restart, act_stop, act_start, act_status
from server_admin.logs import act_logs, act_errors, build_latest_log
from server_admin.system import (
    act_py_version, act_linux_version, act_disk, act_ram, act_cpu, act_ip, act_time, act_sysinfo,
)
from server_admin.github import act_git_status, act_git_pull, act_git_log
from server_admin.backup import (
    act_backup, act_cleanup, build_project_archive, build_db_archive,
    srv_backup_list_show, srv_backup_item,
    srv_backup_restore_confirm, srv_backup_restore_yes,
    srv_backup_delete_confirm, srv_backup_delete_yes,
)


# ── Action registry (callback_data → function) ──────────────────────────────

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

# Rule #7 — disruptive/impactful actions that require an explicit "✅ نعم"
# confirmation before running at all.
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


async def srv_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


# ── Menu / navigation handlers ────────────────────────────────────────────────

async def srv_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else None
    if update.callback_query:
        await update.callback_query.answer()
    if not is_owner(user_id):
        if update.callback_query:
            await update.callback_query.answer(NO_PERMISSION_TEXT, show_alert=True)
        else:
            await update.message.reply_text(NO_PERMISSION_TEXT)
        return ConversationHandler.END
    await reply(update, "🖥 *إدارة السيرفر*\n\nاختر عملية:", _menu_kb())
    return SRV_MENU


async def srv_menu_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.callback_query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return ConversationHandler.END
    await update.callback_query.answer()
    await reply(update, "🖥 *إدارة السيرفر*\n\nاختر عملية:", _menu_kb())
    return SRV_MENU


async def srv_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispatches every plain text-report action (with confirm-gating for
    disruptive ones, rule #7) and every download action."""
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU

    action = query.data

    if action in DOWNLOAD_BUILDERS:
        await execute_download(update, context, DOWNLOAD_TITLES[action], DOWNLOAD_BUILDERS[action])
        return SRV_MENU

    if action in CONFIRM_REQUIRED:
        await query.answer()
        label = ACTION_TITLES.get(action, action)
        short = action.replace("srv_", "", 1)
        await reply(
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
    await execute_with_progress(update, context, ACTION_TITLES.get(action, action), func)
    return SRV_MENU


async def srv_confirm_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    action = "srv_" + query.data.replace("srv_yes_", "", 1)
    func = ACTION_FUNCS.get(action)
    if not func:
        await query.answer()
        return SRV_MENU
    await execute_with_progress(update, context, ACTION_TITLES.get(action, action), func)
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
