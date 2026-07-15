# -*- coding: utf-8 -*-
"""
Admin side of the fully independent '💡 إدارة المبادرات' (Initiatives) system.

Design notes (mirrors the quiz/distro admin modules for consistency):
- Its OWN ConversationHandler, entered from the admin main menu via the
  "adm_initiatives" callback button (the only line added to admin.py).
- All data lives in initiatives_storage.py (its own JSON files). Balance
  changes on "تم التنفيذ" go through the existing credits.py/transactions.py
  helpers, exactly like the quiz-results-crediting flow — nothing here
  touches days.json/users.json/quizzes.json/etc. directly.
- Achievement checks (أول مبادر / مبادر نشيط / مبادر متميز) are delegated to
  achievements_storage.py so this file never needs to know badge internals.
"""
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler,
    MessageHandler, CallbackQueryHandler, filters,
)
from telegram.constants import ParseMode

import admins_store
from storage import get_user
import initiatives_storage as ins
import credits
import transactions

logger = logging.getLogger(__name__)

# ── States ────────────────────────────────────────────────────────────────────
(IN_HUB, IN_LIST, IN_ITEM_MENU, IN_DEL_CONFIRM,
 IN_C_NAME, IN_C_DESC, IN_C_POINTS, IN_C_VISIBLE,
 IN_EDIT_MENU, IN_E_NAME, IN_E_DESC, IN_E_POINTS,
 IN_REQ_FILTER, IN_REQ_LIST, IN_REQ_DETAIL,
 ) = range(15)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_admin(uid: int) -> bool:
    return admins_store.is_admin(uid)


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


def _yn(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ نعم", callback_data=yes_cb),
        InlineKeyboardButton("❌ لا", callback_data=no_cb),
    ]])


# ── Hub ───────────────────────────────────────────────────────────────────────

def _hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إنشاء مبادرة", callback_data="in_create")],
        [InlineKeyboardButton("📋 قائمة المبادرات", callback_data="in_list_view")],
        [InlineKeyboardButton("✏️ تعديل مبادرة", callback_data="in_list_edit")],
        [InlineKeyboardButton("🗑 حذف مبادرة", callback_data="in_list_delete")],
        [InlineKeyboardButton("👁 إظهار / إخفاء المبادرة", callback_data="in_list_toggle")],
        [InlineKeyboardButton("📊 طلبات التنفيذ", callback_data="in_requests")],
        [InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="adm_main")],
    ])


async def in_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer("⛔ ليس لديك صلاحية.", show_alert=True)
        return ConversationHandler.END
    if update.callback_query:
        await update.callback_query.answer()
    context.user_data.pop("in_initiative_id", None)
    context.user_data.pop("in_action", None)
    await _reply(update, "💡 *إدارة المبادرات*\n\nاختر العملية:", _hub_kb())
    return IN_HUB


# ── Initiative list (shared by view/edit/delete/toggle actions) ─────────────

def _list_kb() -> InlineKeyboardMarkup:
    items = ins.load_initiatives()
    rows = [
        [InlineKeyboardButton(v.get("name", "—"), callback_data=f"in_pick_{k}")]
        for k, v in sorted(items.items(), key=lambda kv: int(kv[0]))
    ]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="in_hub")])
    return InlineKeyboardMarkup(rows)


async def in_list_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.replace("in_list_", "")  # view | edit | delete | toggle
    context.user_data["in_action"] = action

    items = ins.load_initiatives()
    if not items:
        await _reply(
            update, "📭 لا توجد مبادرات بعد.\n\nأنشئ مبادرة جديدة أولاً.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="in_hub")]]),
        )
        return IN_LIST

    await _reply(update, "📋 *قائمة المبادرات*\n\nاختر مبادرة:", _list_kb())
    return IN_LIST


def _initiative_summary(initiative: dict) -> str:
    status = "👁 ظاهرة" if initiative.get("visible") else "🙈 مخفية"
    requests = ins.requests_for_initiative(initiative.get("id"))
    completed = sum(1 for r in requests if r.get("status") == ins.STATUS_COMPLETED)
    return (
        f"💡 *{initiative.get('name')}*\n\n"
        f"{initiative.get('description') or '—'}\n\n"
        f"🏆 النقاط: *{initiative.get('points', 0)}*\n"
        f"الحالة: *{status}*\n"
        f"👥 عدد الطلبات: *{len(requests)}*  —  ✔️ منفَّذة: *{completed}*"
    )


async def in_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    initiative_id = query.data.replace("in_pick_", "")
    context.user_data["in_initiative_id"] = initiative_id
    action = context.user_data.get("in_action", "view")
    initiative = ins.get_initiative(initiative_id)
    if not initiative:
        return await in_hub(update, context)

    if action == "view":
        await _reply(
            update, _initiative_summary(initiative),
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="in_hub")]]),
        )
        return IN_HUB

    if action == "toggle":
        ins.set_initiative_visible(initiative_id, not initiative.get("visible"))
        initiative = ins.get_initiative(initiative_id)
        await _reply(
            update, "✅ تم التحديث.\n\n" + _initiative_summary(initiative),
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="in_hub")]]),
        )
        return IN_HUB

    if action == "delete":
        await _reply(
            update,
            f"🗑 هل تريد حذف مبادرة *{initiative.get('name')}*؟\n\n"
            "سيتم حذف جميع طلباتها أيضاً.",
            _yn("in_delete_yes", "in_hub"),
        )
        return IN_DEL_CONFIRM

    if action == "edit":
        await _reply(update, _initiative_summary(initiative) + "\n\nاختر ما تريد تعديله:", _edit_menu_kb())
        return IN_EDIT_MENU

    return await in_hub(update, context)


async def in_delete_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    initiative_id = context.user_data.get("in_initiative_id")
    ins.delete_initiative(initiative_id)
    await _reply(update, "✅ تم حذف المبادرة.", _hub_kb())
    return IN_HUB


# ── Create new initiative ─────────────────────────────────────────────────────

async def in_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["in_new"] = {}
    await _reply(update, "💡 أرسل *اسم المبادرة*:")
    return IN_C_NAME


async def in_c_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if len(text) < 2:
        await update.message.reply_text("⚠️ الرجاء إدخال اسم صحيح.")
        return IN_C_NAME
    context.user_data["in_new"]["name"] = text
    await update.message.reply_text("🗒 أرسل *وصف المبادرة*:")
    return IN_C_DESC


async def in_c_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["in_new"]["description"] = "" if text == "-" else text
    await update.message.reply_text("🔢 أرسل *عدد النقاط المستحقة عند تنفيذها*:")
    return IN_C_POINTS


async def in_c_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم صحيح أكبر من صفر.")
        return IN_C_POINTS
    context.user_data["in_new"]["points"] = int(text)
    await update.message.reply_text(
        "👁 هل المبادرة ظاهرة للمتسابقين؟",
        reply_markup=_yn("in_vis_yes", "in_vis_no"),
    )
    return IN_C_VISIBLE


async def _finish_create(update: Update, context: ContextTypes.DEFAULT_TYPE, visible: bool):
    data = context.user_data.get("in_new", {})
    ins.create_initiative(
        name=data.get("name", "بدون اسم"),
        description=data.get("description", ""),
        points=data.get("points", 0),
        visible=visible,
    )
    context.user_data.pop("in_new", None)
    await _reply(update, "✅ تم إنشاء المبادرة بنجاح.", _hub_kb())
    return IN_HUB


async def in_vis_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _finish_create(update, context, True)


async def in_vis_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _finish_create(update, context, False)


# ── Edit existing initiative ──────────────────────────────────────────────────

def _edit_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 اسم المبادرة", callback_data="in_e_name")],
        [InlineKeyboardButton("🗒 الوصف", callback_data="in_e_desc")],
        [InlineKeyboardButton("🔢 عدد النقاط", callback_data="in_e_points")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="in_hub")],
    ])


async def in_e_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "📝 أرسل الاسم الجديد:")
    return IN_E_NAME


async def in_e_name_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if len(text) < 2:
        await update.message.reply_text("⚠️ الرجاء إدخال اسم صحيح.")
        return IN_E_NAME
    ins.update_initiative_field(context.user_data.get("in_initiative_id"), name=text)
    await update.message.reply_text("✅ تم تحديث الاسم.", reply_markup=_edit_menu_kb())
    return IN_EDIT_MENU


async def in_e_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "🗒 أرسل الوصف الجديد (أرسل `-` لإفراغه):")
    return IN_E_DESC


async def in_e_desc_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    ins.update_initiative_field(context.user_data.get("in_initiative_id"),
                                 description="" if text == "-" else text)
    await update.message.reply_text("✅ تم تحديث الوصف.", reply_markup=_edit_menu_kb())
    return IN_EDIT_MENU


async def in_e_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "🔢 أرسل عدد النقاط الجديد:")
    return IN_E_POINTS


async def in_e_points_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم صحيح أكبر من صفر.")
        return IN_E_POINTS
    ins.update_initiative_field(context.user_data.get("in_initiative_id"), points=int(text))
    await update.message.reply_text("✅ تم تحديث النقاط.", reply_markup=_edit_menu_kb())
    return IN_EDIT_MENU


# ── Execution requests (📊 طلبات التنفيذ) ──────────────────────────────────

_FILTER_BUTTONS = [
    ("📋 كل الطلبات",     None),
    ("🟡 قيد الانتظار",   ins.STATUS_PENDING),
    ("🟢 قيد التنفيذ",    ins.STATUS_ACCEPTED),
    ("✔ مكتملة",          ins.STATUS_COMPLETED),
    ("❌ مرفوضة",          ins.STATUS_REJECTED),
    ("🚫 ملغاة",           ins.STATUS_CANCELLED),
]
_FILTER_CB = {
    None: "in_reqf_all",
    ins.STATUS_PENDING: "in_reqf_pending",
    ins.STATUS_ACCEPTED: "in_reqf_accepted",
    ins.STATUS_COMPLETED: "in_reqf_completed",
    ins.STATUS_REJECTED: "in_reqf_rejected",
    ins.STATUS_CANCELLED: "in_reqf_cancelled",
}
_CB_TO_STATUS = {v: k for k, v in _FILTER_CB.items()}


def _req_label(r: dict) -> str:
    initiative = ins.get_initiative(r.get("initiative_id"))
    name = initiative.get("name", "—")
    status_label = ins.STATUS_LABELS.get(r.get("status"), r.get("status"))
    return f"{r.get('user_name', '—')} — {name} — {status_label}"


def _filter_menu_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(label, callback_data=_FILTER_CB[status])] for label, status in _FILTER_BUTTONS]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="in_hub")])
    return InlineKeyboardMarkup(rows)


def _req_list_kb(status) -> InlineKeyboardMarkup:
    requests = ins.requests_by_status(status)
    rows = [
        [InlineKeyboardButton(_req_label(r)[:64], callback_data=f"in_req_{r['initiative_id']}_{r['user_id']}")]
        for r in requests
    ]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="in_requests")])
    return InlineKeyboardMarkup(rows)


async def in_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "📊 *طلبات التنفيذ*\n\nاختر التصفية:", _filter_menu_kb())
    return IN_REQ_FILTER


async def in_req_filter_sel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    status = _CB_TO_STATUS.get(query.data)
    context.user_data["in_req_filter"] = status
    return await _show_req_list(update, context)


async def _show_req_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = context.user_data.get("in_req_filter")
    requests = ins.requests_by_status(status)
    if not requests:
        await _reply(
            update, "📭 لا توجد طلبات بهذه الحالة.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="in_requests")]]),
        )
        return IN_REQ_LIST
    await _reply(update, "📊 *طلبات التنفيذ*\n\nمرتبة حسب وقت الطلب (الأول فالأول):", _req_list_kb(status))
    return IN_REQ_LIST


async def in_req_sel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    payload = query.data.replace("in_req_", "")
    initiative_id, user_id = payload.split("_", 1)
    context.user_data["in_req_initiative_id"] = initiative_id
    context.user_data["in_req_user_id"] = user_id

    r = ins.get_request(initiative_id, user_id)
    initiative = ins.get_initiative(initiative_id)
    if not r or not initiative:
        return await _show_req_list(update, context)

    text = (
        f"👤 *{r.get('user_name', '—')}*\n"
        f"💡 المبادرة: *{initiative.get('name')}*\n"
        f"🏆 النقاط: *{initiative.get('points', 0)}*\n"
        f"الحالة: *{ins.STATUS_LABELS.get(r.get('status'), r.get('status'))}*\n"
    )
    if r.get("status") == ins.STATUS_PENDING:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ قبول التنفيذ", callback_data="in_req_accept")],
            [InlineKeyboardButton("❌ رفض الطلب", callback_data="in_req_reject")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="in_req_back")],
        ])
    elif r.get("status") == ins.STATUS_ACCEPTED:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✔️ تم التنفيذ", callback_data="in_req_complete")],
            [InlineKeyboardButton("🚫 إلغاء المبادرة", callback_data="in_req_cancel")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="in_req_back")],
        ])
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="in_req_back")]])
    await _reply(update, text, kb)
    return IN_REQ_DETAIL


async def in_req_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_req_list(update, context)


async def in_req_accept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    initiative_id = context.user_data.get("in_req_initiative_id")
    user_id       = context.user_data.get("in_req_user_id")
    ins.set_request_status(initiative_id, user_id, ins.STATUS_ACCEPTED,
                            decided_at=datetime.utcnow().isoformat())

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text="🟢 تم قبول طلبك، يمكنك البدء بالتنفيذ.",
        )
    except Exception as e:
        logger.warning("initiative accept notify failed: %s", e)

    try:
        import achievements_storage as ach
        ach.check_first_initiative_badge(user_id)
    except Exception as e:
        logger.warning("achievements first_initiative check failed: %s", e)

    await _reply(update, "✅ تم قبول الطلب وإشعار المتسابق.",
                 InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="in_req_back")]]))
    return IN_REQ_DETAIL


async def in_req_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    initiative_id = context.user_data.get("in_req_initiative_id")
    user_id       = context.user_data.get("in_req_user_id")
    ins.set_request_status(initiative_id, user_id, ins.STATUS_REJECTED,
                            decided_at=datetime.utcnow().isoformat())

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text="❌ تم رفض طلب المبادرة.",
        )
    except Exception as e:
        logger.warning("initiative reject notify failed: %s", e)

    await _reply(update, "✅ تم رفض الطلب وإشعار المتسابق.",
                 InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="in_req_back")]]))
    return IN_REQ_DETAIL


async def in_req_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    initiative_id = context.user_data.get("in_req_initiative_id")
    user_id       = context.user_data.get("in_req_user_id")
    ins.cancel_request(initiative_id, user_id)

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text="🚫 تم إلغاء المبادرة.",
        )
    except Exception as e:
        logger.warning("initiative cancel notify failed: %s", e)

    await _reply(update, "✅ تم إلغاء المبادرة وإشعار المتسابق.",
                 InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="in_req_back")]]))
    return IN_REQ_DETAIL


async def in_req_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    initiative_id = context.user_data.get("in_req_initiative_id")
    user_id       = context.user_data.get("in_req_user_id")
    initiative    = ins.get_initiative(initiative_id)
    points        = int(initiative.get("points", 0)) if initiative else 0

    ins.set_request_status(initiative_id, user_id, ins.STATUS_COMPLETED,
                            completed_at=datetime.utcnow().isoformat())

    user_obj  = get_user(int(user_id)) or {}
    full_name = user_obj.get("full_name") or "—"
    bal_before = credits.get_balance(int(user_id))
    bal_after  = credits.add_credits(int(user_id), points)
    transactions.record(
        int(user_id), full_name, "initiative_complete", points, bal_before, bal_after,
        f"تنفيذ مبادرة: {initiative.get('name', '—') if initiative else '—'}",
    )

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
                "✔ تم اعتماد تنفيذ المبادرة، وتمت إضافة نقاطها إلى رصيدك.\n\n"
                f"➕ *{points}* نقطة."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.warning("initiative complete notify failed: %s", e)

    try:
        import achievements_storage as ach
        ach.check_initiative_completion_badges(user_id)
    except Exception as e:
        logger.warning("achievements initiative-completion check failed: %s", e)

    await _reply(update, f"✅ تم احتساب *{points}* نقطة للمتسابق وإشعاره.",
                 InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="in_req_back")]]))
    return IN_REQ_DETAIL


# ── Cancel fallback ───────────────────────────────────────────────────────────

async def in_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END


# ── Build the ConversationHandler ─────────────────────────────────────────────

def build_initiatives_admin_handler() -> ConversationHandler:
    hub_reentry = CallbackQueryHandler(in_hub, pattern="^in_hub$")
    list_entry  = CallbackQueryHandler(in_list_entry, pattern=r"^in_list_(view|edit|delete|toggle)$")

    return ConversationHandler(
        entry_points=[CallbackQueryHandler(in_hub, pattern="^adm_initiatives$")],
        states={
            IN_HUB: [
                CallbackQueryHandler(in_create,   pattern="^in_create$"),
                CallbackQueryHandler(in_requests, pattern="^in_requests$"),
                list_entry,
            ],
            IN_LIST: [
                CallbackQueryHandler(in_pick, pattern=r"^in_pick_\w+$"),
                list_entry, hub_reentry,
            ],
            IN_DEL_CONFIRM: [
                CallbackQueryHandler(in_delete_yes, pattern="^in_delete_yes$"),
                hub_reentry,
            ],
            IN_C_NAME:   [MessageHandler(filters.TEXT & ~filters.COMMAND, in_c_name), hub_reentry],
            IN_C_DESC:   [MessageHandler(filters.TEXT & ~filters.COMMAND, in_c_desc), hub_reentry],
            IN_C_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, in_c_points), hub_reentry],
            IN_C_VISIBLE: [
                CallbackQueryHandler(in_vis_yes, pattern="^in_vis_yes$"),
                CallbackQueryHandler(in_vis_no,  pattern="^in_vis_no$"),
                hub_reentry,
            ],
            IN_EDIT_MENU: [
                CallbackQueryHandler(in_e_name,   pattern="^in_e_name$"),
                CallbackQueryHandler(in_e_desc,   pattern="^in_e_desc$"),
                CallbackQueryHandler(in_e_points, pattern="^in_e_points$"),
                hub_reentry,
            ],
            IN_E_NAME:   [MessageHandler(filters.TEXT & ~filters.COMMAND, in_e_name_val), hub_reentry],
            IN_E_DESC:   [MessageHandler(filters.TEXT & ~filters.COMMAND, in_e_desc_val), hub_reentry],
            IN_E_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, in_e_points_val), hub_reentry],
            IN_REQ_FILTER: [
                CallbackQueryHandler(in_req_filter_sel, pattern=r"^in_reqf_\w+$"),
                hub_reentry,
            ],
            IN_REQ_LIST: [
                CallbackQueryHandler(in_req_sel,   pattern=r"^in_req_\w+_\d+$"),
                CallbackQueryHandler(in_requests,  pattern="^in_requests$"),
                hub_reentry,
            ],
            IN_REQ_DETAIL: [
                CallbackQueryHandler(in_req_accept,   pattern="^in_req_accept$"),
                CallbackQueryHandler(in_req_reject,   pattern="^in_req_reject$"),
                CallbackQueryHandler(in_req_complete,  pattern="^in_req_complete$"),
                CallbackQueryHandler(in_req_cancel,    pattern="^in_req_cancel$"),
                CallbackQueryHandler(in_req_back,      pattern="^in_req_back$"),
                CallbackQueryHandler(in_requests,     pattern="^in_requests$"),
                hub_reentry,
            ],
        },
        fallbacks=[MessageHandler(filters.COMMAND, in_cancel)],
        name="initiatives_admin_conv",
        persistent=False,
        allow_reentry=True,
    )
