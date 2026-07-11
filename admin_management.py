# -*- coding: utf-8 -*-
"""
Admin-management commands — standalone, independent of admin.py's
ConversationHandler (plain CommandHandlers, no shared state).

/addadmin <id>     — owner only: grant someone full admin access
/removeadmin <id>  — owner only: revoke an extra admin's access
/admins            — any admin: list current admins (owner + extras)
"""
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import admins_store as store


async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not store.is_owner(update.effective_user.id):
        await update.message.reply_text("❌ هذا الأمر متاح فقط لمالك البوت.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("الاستخدام:\n/addadmin <telegram_id>")
        return
    new_id = int(context.args[0])
    if store.add_admin(new_id):
        await update.message.reply_text(
            f"✅ تمت إضافة `{new_id}` كمشرف جديد.\nأصبح لديه وصول كامل للوحة التحكم.",
            parse_mode=ParseMode.MARKDOWN,
        )
        try:
            await context.bot.send_message(
                chat_id=new_id,
                text="🎉 تمت إضافتك كمشرف في البوت.\n\nاستخدم /admin لفتح لوحة التحكم.",
            )
        except Exception:
            pass  # the user may not have started a chat with the bot yet
    else:
        await update.message.reply_text(f"⚠️ `{new_id}` مشرف بالفعل.", parse_mode=ParseMode.MARKDOWN)


async def remove_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not store.is_owner(update.effective_user.id):
        await update.message.reply_text("❌ هذا الأمر متاح فقط لمالك البوت.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("الاستخدام:\n/removeadmin <telegram_id>")
        return
    target_id = int(context.args[0])
    if store.is_owner(target_id):
        await update.message.reply_text("⚠️ لا يمكن إزالة مالك البوت.")
        return
    if store.remove_admin(target_id):
        await update.message.reply_text(f"✅ تمت إزالة `{target_id}` من قائمة المشرفين.",
                                         parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"⚠️ `{target_id}` ليس ضمن المشرفين الإضافيين.",
                                         parse_mode=ParseMode.MARKDOWN)


async def list_admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not store.is_admin(update.effective_user.id):
        await update.message.reply_text("❌ ليس لديك صلاحية لاستخدام هذا الأمر.")
        return
    lines = ["👥 *قائمة المشرفين*\n"]
    if store.ADMIN_ID:
        lines.append(f"👑 المالك: `{store.ADMIN_ID}`")
    extras = store.load_extra_admins()
    if extras:
        lines.append("\n*مشرفون إضافيون:*")
        lines += [f"• `{uid}`" for uid in extras]
    else:
        lines.append("\nلا يوجد مشرفون إضافيون حالياً.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
