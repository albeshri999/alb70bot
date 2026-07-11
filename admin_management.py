# -*- coding: utf-8 -*-
"""
Admin-management commands — standalone, independent of admin.py's
ConversationHandler (plain CommandHandlers, no shared state).

These are a command-line-style equivalent of the "⚙️ إعدادات المشرفين"
button flow in admin_settings.py — both operate on the same
admins_store.py data.

/addadmin <id>     — owner only: grant someone restricted admin access
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

    if store.is_admin(new_id):
        await update.message.reply_text("⚠️ هذا المستخدم مشرف بالفعل.")
        return

    name = username = ""
    try:
        chat = await context.bot.get_chat(new_id)
        name     = chat.full_name or ""
        username = chat.username or ""
    except Exception:
        pass  # bot may not have seen this user yet — still fine to add by ID

    store.add_admin(new_id, name=name, username=username,
                    added_by=update.effective_user.id)
    await update.message.reply_text("✅ تمت إضافة المشرف بنجاح.")
    try:
        await context.bot.send_message(
            chat_id=new_id,
            text="🎉 تمت إضافتك كمشرف في البوت.\n\nاستخدم /admin لفتح لوحة التحكم.",
        )
    except Exception:
        pass  # the user may not have started a chat with the bot yet


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
        for rec in extras.values():
            name = rec.get("name") or "—"
            date = (rec.get("added_at") or "—")[:16].replace("T", " ")
            lines.append(f"• *{name}*\n   `{rec.get('telegram_id')}`  —  أُضيف: {date}")
    else:
        lines.append("\nلا يوجد مشرفون إضافيون حالياً.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
