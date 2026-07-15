# -*- coding: utf-8 -*-
"""
'⚙️ إعدادات المشرفين' — owner-only admin-management screen.

Design (same pattern as quiz_admin.py):
- Its own, fully independent ConversationHandler, entered via the
  "adm_admins" callback button (only shown to the owner in admin.py's main
  keyboard — see admin.py's _main_kb()).
- Because python-telegram-bot's ConversationHandler ignores updates that
  don't match its current state instead of consuming them, pressing
  "⬅️ القائمة الرئيسية" (callback_data="adm_main") falls through untouched
  to the original admin ConversationHandler (still parked in its MAIN
  state), which shows the normal admin main menu exactly as before.
- All data lives in admins_store.py (its own JSON file). Nothing here reads
  or writes any other data file.
- Every entry point re-checks store.is_owner() — even though the button is
  hidden from non-owners, this blocks any crafted/replayed callback too.
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters,
)
from telegram.constants import ParseMode

import admins_store as store
from storage import load_users, get_user

logger = logging.getLogger(__name__)

(AS_HUB, AS_ADD_MENU, AS_ADD, AS_ADD_PICK_LIST, AS_ADD_PICK_CONFIRM,
 AS_DEL_LIST, AS_DEL_CONFIRM) = range(7)


def _md(text: str) -> str:
    for ch in ("_", "*", "`", "[", "]"):
        text = str(text).replace(ch, f"\\{ch}")
    return text


async def _reply(update: Update, text: str, keyboard=None):
    kw = {"text": text, "parse_mode": ParseMode.MARKDOWN}
    if keyboard:
        kw["reply_markup"] = keyboard
    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(**kw)
        except Exception as e1:
            logger.warning("_reply edit_text failed: %s", e1)
            try:
                await update.callback_query.message.reply_text(**kw)
            except Exception as e2:
                logger.error("_reply reply_text also failed: %s", e2)
    else:
        await update.message.reply_text(**kw)


def _hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة مشرف", callback_data="as_add_menu")],
        [InlineKeyboardButton("👥 قائمة المشرفين", callback_data="as_list")],
        [InlineKeyboardButton("🗑 حذف مشرف", callback_data="as_del_list")],
        [InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="adm_main")],
    ])


async def as_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not store.is_owner(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer("⛔ هذا الخيار متاح فقط لمالك البوت.", show_alert=True)
        return ConversationHandler.END
    if update.callback_query:
        await update.callback_query.answer()
    context.user_data.pop("as_target", None)
    await _reply(update, "⚙️ *إعدادات المشرفين*\n\nاختر العملية:", _hub_kb())
    return AS_HUB


# ── Add admin ─────────────────────────────────────────────────────────────────
#
# "➕ إضافة مشرف" now offers two methods:
#   1. 🆔 Telegram ID — exactly the original flow, unchanged (as_add_by_id).
#   2. 👥 اختيار من المشاركين — a new paginated picker over every user who has
#      ever used the bot (storage.load_users()), added purely as a more
#      convenient alternative that ends up calling the SAME store.add_admin()
#      used by the ID method — so both paths are equivalent in every way
#      that matters (permissions, notifications, storage).

_ADD_PICK_PER_PAGE = 20


def _add_method_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆔 إضافة بواسطة Telegram ID", callback_data="as_add_by_id")],
        [InlineKeyboardButton("👥 اختيار من المشاركين", callback_data="as_add_pick_list")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="as_hub")],
    ])


async def as_add_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "➕ *إضافة مشرف*\n\nاختر طريقة الإضافة:", _add_method_kb())
    return AS_ADD_MENU


# ── Method 1: by Telegram ID (unchanged from before) ─────────────────────────

async def as_add_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "🆔 أرسل Telegram ID للمشرف الجديد:")
    return AS_ADD


async def as_add_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("⚠️ الرجاء إرسال رقم Telegram ID صحيح.")
        return AS_ADD

    new_id = int(text)
    if store.is_admin(new_id):
        await update.message.reply_text("⚠️ هذا المستخدم مشرف بالفعل.")
        return await _return_to_hub_via_message(update, context)

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

    return await _return_to_hub_via_message(update, context)


async def _return_to_hub_via_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-show the hub after a text-based step (no callback_query available)."""
    await update.message.reply_text("⚙️ *إعدادات المشرفين*\n\nاختر العملية:",
                                     reply_markup=_hub_kb(), parse_mode=ParseMode.MARKDOWN)
    return AS_HUB


# ── Method 2: pick from the bot's participants ───────────────────────────────

def _sorted_users() -> list:
    users = load_users()
    return sorted(users.items(), key=lambda x: (x[1].get("full_name") or "").strip().lower())


def _add_pick_kb(users_list: list, page: int) -> InlineKeyboardMarkup:
    start = page * _ADD_PICK_PER_PAGE
    shown = users_list[start:start + _ADD_PICK_PER_PAGE]
    total = len(users_list)

    rows = []
    for uid, u in shown:
        name  = u.get("full_name") or "—"
        uname = f" (@{u['username']})" if u.get("username") else ""
        label = f"👤 {name}{uname}"[:64]
        rows.append([InlineKeyboardButton(label, callback_data=f"as_addpick_{uid}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"as_addpick_page_{page - 1}"))
    if start + _ADD_PICK_PER_PAGE < total:
        nav.append(InlineKeyboardButton("➡️ التالي", callback_data=f"as_addpick_page_{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="as_add_menu")])
    return InlineKeyboardMarkup(rows)


async def _show_add_pick_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    users_list = _sorted_users()
    if not users_list:
        await _reply(
            update, "📭 لا يوجد أي مستخدمين للبوت بعد.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="as_add_menu")]]),
        )
        return AS_ADD_MENU

    context.user_data["as_add_page"] = page
    total       = len(users_list)
    total_pages = max(1, (total + _ADD_PICK_PER_PAGE - 1) // _ADD_PICK_PER_PAGE)
    await _reply(
        update,
        f"👥 *اختيار من المشاركين*\n\n"
        f"صفحة *{page + 1}* من *{total_pages}*  —  الإجمالي *{total}*\n\n"
        "اختر مستخدماً لتعيينه مشرفاً:",
        _add_pick_kb(users_list, page),
    )
    return AS_ADD_PICK_LIST


async def as_add_pick_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_add_pick_list(update, context, page=0)


async def as_addpick_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    page = int(update.callback_query.data.replace("as_addpick_page_", ""))
    return await _show_add_pick_list(update, context, page=page)


async def as_addpick_sel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = int(update.callback_query.data.replace("as_addpick_", ""))

    if store.is_admin(uid):
        await _reply(
            update, "⚠️ هذا المستخدم مشرف بالفعل.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="as_add_pick_list")]]),
        )
        return AS_ADD_PICK_LIST

    context.user_data["as_target"] = uid
    user  = get_user(uid) or {}
    name  = user.get("full_name") or "—"
    await _reply(
        update,
        "هل تريد تعيين هذا المستخدم مشرفاً؟\n\n"
        f"👤 *الاسم:*\n{_md(name)}\n\n"
        f"🆔 *Telegram ID:*\n`{uid}`",
        InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ نعم", callback_data="as_addpick_yes"),
            InlineKeyboardButton("❌ لا",  callback_data="as_addpick_no"),
        ]]),
    )
    return AS_ADD_PICK_CONFIRM


async def as_addpick_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = context.user_data.pop("as_target", None)
    if uid is None:
        await _reply(update, "⚠️ حدث خطأ. حاول مجدداً.", _hub_kb())
        return AS_HUB
    if store.is_admin(uid):
        await _reply(update, "⚠️ هذا المستخدم مشرف بالفعل.", _hub_kb())
        return AS_HUB

    user     = get_user(uid) or {}
    name     = user.get("full_name") or ""
    username = user.get("username") or ""
    store.add_admin(uid, name=name, username=username, added_by=update.effective_user.id)

    await _reply(update, "✅ تمت إضافة المشرف بنجاح.", _hub_kb())
    try:
        await context.bot.send_message(
            chat_id=uid,
            text="🎉 تمت إضافتك كمشرف في البوت.\n\nاستخدم /admin لفتح لوحة التحكم.",
        )
    except Exception:
        pass  # the user may not have started a chat with the bot yet
    return AS_HUB


async def as_addpick_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data.pop("as_target", None)
    return await _show_add_pick_list(update, context, page=context.user_data.get("as_add_page", 0))


# ── List admins ───────────────────────────────────────────────────────────────

def _admins_list_text() -> str:
    lines = ["👥 *قائمة المشرفين*\n"]
    if store.ADMIN_ID:
        lines.append(f"👑 *المالك*\n`{store.ADMIN_ID}`")
    extras = store.load_extra_admins()
    if extras:
        lines.append("\n*مشرفون إضافيون:*")
        for rec in extras.values():
            name = _md(rec.get("name") or "—")
            date = (rec.get("added_at") or "—")[:16].replace("T", " ")
            lines.append(
                f"\n👤 *{name}*\n"
                f"🆔 `{rec.get('telegram_id')}`\n"
                f"📅 تاريخ الإضافة: {date}"
            )
    else:
        lines.append("\nلا يوجد مشرفون إضافيون حالياً.")
    return "\n".join(lines)


async def as_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="as_hub")]])
    await _reply(update, _admins_list_text(), kb)
    return AS_HUB


# ── Delete admin ──────────────────────────────────────────────────────────────

async def as_del_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    extras = store.load_extra_admins()
    if not extras:
        await _reply(update, "📭 لا يوجد مشرفون إضافيون لحذفهم.",
                     InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="as_hub")]]))
        return AS_HUB

    rows = []
    for rec in extras.values():
        uid   = rec.get("telegram_id")
        label = rec.get("name") or str(uid)
        rows.append([InlineKeyboardButton(f"{label}  ({uid})", callback_data=f"as_pick_{uid}")])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="as_hub")])
    await _reply(update, "🗑 *حذف مشرف*\n\nاختر المشرف المراد حذفه:", InlineKeyboardMarkup(rows))
    return AS_DEL_LIST


async def as_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    target_id = int(update.callback_query.data.replace("as_pick_", ""))
    context.user_data["as_target"] = target_id

    if store.is_owner(target_id):
        await _reply(update, "⚠️ لا يمكن حذف مالك البوت.", _hub_kb())
        return AS_HUB

    extras = store.load_extra_admins()
    rec    = extras.get(str(target_id), {})
    name   = rec.get("name") or str(target_id)
    await _reply(
        update,
        f"🗑 هل تريد حذف هذا المشرف؟\n\n👤 *{_md(name)}*\n🆔 `{target_id}`",
        InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ نعم", callback_data="as_del_yes"),
            InlineKeyboardButton("❌ لا",  callback_data="as_del_no"),
        ]]),
    )
    return AS_DEL_CONFIRM


async def as_del_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    target_id = context.user_data.pop("as_target", None)
    if target_id is None or store.is_owner(target_id):
        await _reply(update, "⚠️ لا يمكن تنفيذ هذا الإجراء.", _hub_kb())
        return AS_HUB
    store.remove_admin(target_id)
    await _reply(update, "✅ تم حذف المشرف بنجاح.", _hub_kb())
    return AS_HUB


async def as_del_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data.pop("as_target", None)
    await _reply(update, "⚙️ *إعدادات المشرفين*\n\nاختر العملية:", _hub_kb())
    return AS_HUB


# ── Cancel fallback ───────────────────────────────────────────────────────────

async def as_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END


# ── Build the ConversationHandler ─────────────────────────────────────────────

def build_admin_settings_handler() -> ConversationHandler:
    hub_reentry = CallbackQueryHandler(as_hub, pattern="^as_hub$")

    return ConversationHandler(
        entry_points=[CallbackQueryHandler(as_hub, pattern="^adm_admins$")],
        states={
            AS_HUB: [
                CallbackQueryHandler(as_add_menu, pattern="^as_add_menu$"),
                CallbackQueryHandler(as_list,     pattern="^as_list$"),
                CallbackQueryHandler(as_del_list, pattern="^as_del_list$"),
                hub_reentry,
            ],
            AS_ADD_MENU: [
                CallbackQueryHandler(as_add_by_id,     pattern="^as_add_by_id$"),
                CallbackQueryHandler(as_add_pick_list, pattern="^as_add_pick_list$"),
                hub_reentry,
            ],
            AS_ADD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, as_add_val),
                hub_reentry,
            ],
            AS_ADD_PICK_LIST: [
                CallbackQueryHandler(as_addpick_sel,  pattern=r"^as_addpick_\d+$"),
                CallbackQueryHandler(as_addpick_page, pattern=r"^as_addpick_page_\d+$"),
                CallbackQueryHandler(as_add_menu,     pattern="^as_add_menu$"),
                CallbackQueryHandler(as_add_pick_list, pattern="^as_add_pick_list$"),
                hub_reentry,
            ],
            AS_ADD_PICK_CONFIRM: [
                CallbackQueryHandler(as_addpick_yes,  pattern="^as_addpick_yes$"),
                CallbackQueryHandler(as_addpick_no,   pattern="^as_addpick_no$"),
                hub_reentry,
            ],
            AS_DEL_LIST: [
                CallbackQueryHandler(as_pick, pattern=r"^as_pick_\d+$"),
                hub_reentry,
            ],
            AS_DEL_CONFIRM: [
                CallbackQueryHandler(as_del_yes, pattern="^as_del_yes$"),
                CallbackQueryHandler(as_del_no,  pattern="^as_del_no$"),
                hub_reentry,
            ],
        },
        fallbacks=[CommandHandler("cancel", as_cancel)],
        allow_reentry=True,
        per_message=False,
    )
