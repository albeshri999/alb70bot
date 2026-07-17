# -*- coding: utf-8 -*-
"""
server_admin.menu — the presentation/wiring layer (Clean Architecture's
"composition root" for this package).

Redesigned for phone-first daily use (production charter rules #24/#28):
the previous flat list of 26+ buttons is now 9 top-level entries. Related
actions are grouped into one-tap sub-pages instead of separate top-level
buttons:
  - ⚡ حالة وتحكم الخدمة  → status + restart/stop/start together
  - 📜 السجلات            → normal logs / error logs
  - 🔧 الصيانة            → pip update / cache clean / code check
  - 📁 تنزيل الملفات      → project / database / latest log
  - 🖥 معلومات وتشخيص    → ONE combined report (was 7 system buttons + 2
                            git buttons) — no sub-page needed, it's just
                            informational and safe to show in one tap.
  - ⬅ Git Pull was removed entirely — 🚀 نشر تحديث (Deploy) already pulls
    the latest code internally via scripts/deploy.sh with a proper backup
    and health-check around it; a second, unsafe way to update code would
    just be confusing and redundant.

No business logic lives here — every action function comes from its own
domain module (deploy.py, rollback.py, update.py, monitor.py, logs.py,
system.py, backup.py); this module only wires callback_data to those
functions and renders the menus.
"""
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, filters

from server_admin.utils import (
    is_owner, reply, NO_PERMISSION_TEXT,
    SRV_MENU, SRV_CONFIRM, SRV_BACKUP_LIST, SRV_BACKUP_ITEM, SRV_BACKUP_CONFIRM,
    SRV_STATUS_MENU, SRV_LOGS_MENU, SRV_MAINT_MENU, SRV_DL_MENU,
    SRV_UPDATE_MENU, SRV_UPDATE_AWAIT_ZIP, SRV_UPDATE_ZIP_REVIEW,
)
from server_admin.notifications import execute_with_progress, execute_download

from server_admin.deploy import act_deploy
from server_admin.rollback import act_rollback
from server_admin.update import act_pip_update, act_cache_clean, act_codecheck
from server_admin.monitor import act_restart, act_stop, act_start, act_status
from server_admin.logs import act_logs, act_errors, build_latest_log
from server_admin.system import act_sysinfo
from server_admin.backup import (
    act_backup, act_cleanup, build_project_archive, build_db_archive,
    srv_backup_list_show, srv_backup_item,
    srv_backup_restore_confirm, srv_backup_restore_yes,
    srv_backup_delete_confirm, srv_backup_delete_yes,
)
from server_admin.zip_update import (
    GITHUB_DEPLOY_TITLE,
    srv_update_menu, srv_update_history, srv_update_zip_start, srv_update_zip_receive,
    srv_zip_list, srv_zip_cancel, srv_zip_install,
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
    "srv_sysinfo":         act_sysinfo,
}

ACTION_TITLES = {
    "srv_deploy":          GITHUB_DEPLOY_TITLE,
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
    "srv_sysinfo":         "🖥 معلومات وتشخيص",
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


# ── Top-level menu (9 entries — was 26+) ────────────────────────────────────

def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 إدارة التحديثات", callback_data="srv_update_menu")],
        [InlineKeyboardButton("⚡ حالة وتحكم الخدمة", callback_data="srv_page_status")],
        [InlineKeyboardButton("📜 السجلات", callback_data="srv_page_logs")],
        [InlineKeyboardButton("💾 النسخ الاحتياطية", callback_data="srv_list_backups")],
        [InlineKeyboardButton("🔧 الصيانة", callback_data="srv_page_maint")],
        [InlineKeyboardButton("📁 تنزيل الملفات", callback_data="srv_page_downloads")],
        [InlineKeyboardButton("🖥 معلومات وتشخيص", callback_data="srv_sysinfo")],
        [InlineKeyboardButton("⬅ رجوع", callback_data="adm_main")],
    ])


async def srv_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


# ── Main hub navigation ───────────────────────────────────────────────────────

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


# ── Sub-page: ⚡ حالة وتحكم الخدمة (status + restart/stop/start together) ───

def _status_page_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 إعادة تشغيل", callback_data="srv_restart")],
        [InlineKeyboardButton("⛔ إيقاف", callback_data="srv_stop"),
         InlineKeyboardButton("▶ تشغيل", callback_data="srv_start")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="srv_menu")],
    ])


async def srv_page_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    await query.answer()
    await reply(update, "⌛ جاري فحص الحالة...")
    _, status_out = await asyncio.to_thread(act_status)
    text = f"⚡ *حالة وتحكم الخدمة*\n\n{status_out}"
    await reply(update, text, _status_page_kb())
    return SRV_STATUS_MENU


# ── Sub-page: 📜 السجلات ─────────────────────────────────────────────────────

def _logs_page_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📜 آخر 50 سجل", callback_data="srv_logs")],
        [InlineKeyboardButton("❌ آخر الأخطاء", callback_data="srv_errors")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="srv_menu")],
    ])


async def srv_page_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    await query.answer()
    await reply(update, "📜 *السجلات*\n\nاختر:", _logs_page_kb())
    return SRV_LOGS_MENU


# ── Sub-page: 🔧 الصيانة ─────────────────────────────────────────────────────

def _maint_page_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 تحديث المكتبات", callback_data="srv_pip_update")],
        [InlineKeyboardButton("🧹 تنظيف Cache", callback_data="srv_cache_clean")],
        [InlineKeyboardButton("🧪 فحص الكود", callback_data="srv_codecheck")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="srv_menu")],
    ])


async def srv_page_maint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    await query.answer()
    await reply(update, "🔧 *الصيانة*\n\nاختر:", _maint_page_kb())
    return SRV_MAINT_MENU


# ── Sub-page: 📁 تنزيل الملفات ───────────────────────────────────────────────

def _downloads_page_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 المشروع كاملاً", callback_data="srv_download_project")],
        [InlineKeyboardButton("📤 قاعدة البيانات", callback_data="srv_download_db")],
        [InlineKeyboardButton("📄 آخر سجل", callback_data="srv_download_log")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="srv_menu")],
    ])


async def srv_page_downloads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer(NO_PERMISSION_TEXT, show_alert=True)
        return SRV_MENU
    await query.answer()
    await reply(update, "📁 *تنزيل الملفات*\n\nاختر:", _downloads_page_kb())
    return SRV_DL_MENU


# ── Generic dispatch (shared by every sub-page and the main menu alike) ────

async def srv_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispatches every plain text-report action (with confirm-gating for
    disruptive ones, rule #7) and every download action — reachable
    identically from the main menu or from any sub-page, since callback_data
    never changes regardless of which screen it was tapped from."""
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
    # Shared by the main menu AND every sub-page — same callback_data always
    # routes to the same function no matter which screen it was tapped from.
    simple_action_pattern = (
        r"^srv_("
        r"deploy|rollback|restart|stop|start|status|logs|errors|"
        r"backup|cleanup|pip_update|cache_clean|codecheck|sysinfo|"
        r"download_project|download_db|download_log"
        r")$"
    )
    confirm_pattern = r"^srv_yes_(deploy|rollback|restart|stop|cleanup|pip_update)$"

    action_handlers = [
        CallbackQueryHandler(srv_noop, pattern="^srv_noop$"),
        CallbackQueryHandler(srv_menu_back, pattern="^srv_menu$"),
        CallbackQueryHandler(srv_button, pattern=simple_action_pattern),
    ]

    return ConversationHandler(
        entry_points=[CallbackQueryHandler(srv_hub, pattern="^adm_server$")],
        states={
            SRV_MENU: [
                CallbackQueryHandler(srv_update_menu, pattern="^srv_update_menu$"),
                CallbackQueryHandler(srv_page_status, pattern="^srv_page_status$"),
                CallbackQueryHandler(srv_page_logs, pattern="^srv_page_logs$"),
                CallbackQueryHandler(srv_page_maint, pattern="^srv_page_maint$"),
                CallbackQueryHandler(srv_page_downloads, pattern="^srv_page_downloads$"),
                CallbackQueryHandler(srv_backup_list_show, pattern="^srv_list_backups$"),
                *action_handlers,
            ],
            SRV_UPDATE_MENU: [
                CallbackQueryHandler(srv_update_menu, pattern="^srv_update_menu$"),
                CallbackQueryHandler(srv_update_zip_start, pattern="^srv_update_zip_start$"),
                CallbackQueryHandler(srv_update_history, pattern="^srv_update_history$"),
                *action_handlers,
            ],
            SRV_UPDATE_AWAIT_ZIP: [
                CallbackQueryHandler(srv_update_menu, pattern="^srv_update_menu$"),
                MessageHandler(filters.Document.ALL, srv_update_zip_receive),
            ],
            SRV_UPDATE_ZIP_REVIEW: [
                CallbackQueryHandler(srv_zip_install, pattern="^srv_zip_install$"),
                CallbackQueryHandler(srv_zip_list, pattern="^srv_zip_list$"),
                CallbackQueryHandler(srv_zip_cancel, pattern="^srv_zip_cancel$"),
            ],
            SRV_STATUS_MENU: [
                CallbackQueryHandler(srv_page_status, pattern="^srv_page_status$"),
                *action_handlers,
            ],
            SRV_LOGS_MENU: [
                CallbackQueryHandler(srv_page_logs, pattern="^srv_page_logs$"),
                *action_handlers,
            ],
            SRV_MAINT_MENU: [
                CallbackQueryHandler(srv_page_maint, pattern="^srv_page_maint$"),
                *action_handlers,
            ],
            SRV_DL_MENU: [
                CallbackQueryHandler(srv_page_downloads, pattern="^srv_page_downloads$"),
                *action_handlers,
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
