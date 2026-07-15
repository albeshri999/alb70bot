# -*- coding: utf-8 -*-
"""
Admin side of the fully independent '💡 إدارة المبادرات' (Initiatives) system.

Design notes (mirrors quiz_admin.py's per-item detail/management page style,
per the 'إعادة تصميم واجهة إدارة المبادرات' request):
- Its OWN ConversationHandler, entered from the admin main menu via the
  "adm_initiatives" callback button (the only line added to admin.py).
- The hub only has: ➕ إنشاء مبادرة / 📋 قائمة المبادرات / 📊 طلبات التنفيذ / 🔙 رجوع.
- Picking an initiative from the list always opens its own detail/management
  page (name, description, points, capacity info, visibility, status,
  creation date) with buttons: ✏️ تعديل / 👁 إظهار-إخفاء / 🧹 إدارة النتائج /
  🗑 حذف / 🔙 رجوع — exactly mirroring how quiz_admin.py's per-quiz menu works.
- All data lives in initiatives_storage.py (its own JSON files). Balance
  changes on "تم التنفيذ" (and their reversal when an admin deletes a
  completed result) go through the existing credits.py/transactions.py
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
(IN_HUB, IN_LIST, IN_DETAIL, IN_DEL_CONFIRM,
 IN_C_NAME, IN_C_DESC, IN_C_POINTS, IN_C_MAX, IN_C_VISIBLE,
 IN_EDIT_MENU, IN_E_NAME, IN_E_DESC, IN_E_POINTS, IN_E_MAX,
 IN_VIS_MENU,
 IN_EXEC_LIST, IN_EXEC_DETAIL, IN_EXEC_DEL_CONFIRM,
 IN_RES_MENU, IN_RES_LIST, IN_RES_DETAIL,
 IN_REQ_FILTER, IN_REQ_LIST, IN_REQ_DETAIL,
 ) = range(24)


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


def _fmt_date(iso_str) -> str:
    if not iso_str:
        return "—"
    try:
        return datetime.fromisoformat(iso_str).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"


# ── Hub ───────────────────────────────────────────────────────────────────────

def _hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إنشاء مبادرة", callback_data="in_create")],
        [InlineKeyboardButton("📋 قائمة المبادرات", callback_data="in_list")],
        [InlineKeyboardButton("📊 طلبات التنفيذ", callback_data="in_requests")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="adm_main")],
    ])


async def in_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer("⛔ ليس لديك صلاحية.", show_alert=True)
        return ConversationHandler.END
    if update.callback_query:
        await update.callback_query.answer()
    context.user_data.pop("in_initiative_id", None)
    context.user_data.pop("in_res_status", None)
    await _reply(update, "💡 *إدارة المبادرات*\n\nاختر العملية:", _hub_kb())
    return IN_HUB


# ── Initiatives list ─────────────────────────────────────────────────────────

def _list_kb() -> InlineKeyboardMarkup:
    items = ins.load_initiatives()
    rows = [
        [InlineKeyboardButton(v.get("name", "—"), callback_data=f"in_pick_{k}")]
        for k, v in sorted(items.items(), key=lambda kv: int(kv[0]))
    ]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="in_hub")])
    return InlineKeyboardMarkup(rows)


async def _show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = ins.load_initiatives()
    if not items:
        await _reply(
            update, "📭 لا توجد مبادرات بعد.\n\nأنشئ مبادرة جديدة أولاً.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="in_hub")]]),
        )
        return IN_LIST
    await _reply(update, "📋 *قائمة المبادرات*\n\nاختر مبادرة:", _list_kb())
    return IN_LIST


async def in_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_list(update, context)


async def in_back_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'رجوع' target for the per-initiative detail page — always returns to
    the initiatives list (its logical parent screen)."""
    await update.callback_query.answer()
    return await _show_list(update, context)


# ── Per-initiative detail / management page ──────────────────────────────────

def _initiative_detail_text(initiative: dict) -> str:
    requests  = ins.requests_for_initiative(initiative.get("id"))
    pending   = sum(1 for r in requests if r.get("status") == ins.STATUS_PENDING)
    max_p     = ins.get_max_participants(initiative)
    max_line  = str(max_p) if max_p else "بدون حد أقصى"
    remaining = ins.remaining_seats(initiative)
    remaining_line = str(remaining) if remaining is not None else "بدون حد أقصى"
    visibility = "✅ ظاهرة" if initiative.get("visible") else "🙈 مخفية"
    return (
        f"💡 *{initiative.get('name')}*\n\n"
        f"📄 {initiative.get('description') or '—'}\n\n"
        f"🏆 عدد النقاط: *{initiative.get('points', 0)}*\n"
        f"👥 الحد الأقصى للمشاركين: *{max_line}*\n"
        f"🟢 عدد المنفذين الحاليين: *{ins.accepted_count(initiative.get('id'))}*\n"
        f"📨 عدد الطلبات: *{pending}*\n"
        f"📌 المقاعد المتبقية: *{remaining_line}*\n"
        f"👁 حالة الظهور: *{visibility}*\n"
        f"📍 حالة المبادرة: *{ins.initiative_status_label(initiative)}*\n"
        f"📅 تاريخ الإنشاء: {_fmt_date(initiative.get('created_at'))}"
    )


def _detail_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تعديل المبادرة", callback_data="in_detail_edit")],
        [InlineKeyboardButton("👁 إظهار / إخفاء المبادرة", callback_data="in_detail_vis")],
        [InlineKeyboardButton("👥 المنفذون", callback_data="in_detail_execs")],
        [InlineKeyboardButton("🧹 إدارة النتائج", callback_data="in_detail_results")],
        [InlineKeyboardButton("🗑 حذف المبادرة", callback_data="in_detail_delete")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="in_back_to_list")],
    ])


async def _show_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, prefix: str = ""):
    initiative_id = context.user_data.get("in_initiative_id")
    initiative = ins.get_initiative(initiative_id)
    if not initiative:
        return await _show_list(update, context)
    await _reply(update, prefix + _initiative_detail_text(initiative), _detail_kb())
    return IN_DETAIL


async def in_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    initiative_id = query.data.replace("in_pick_", "")
    context.user_data["in_initiative_id"] = initiative_id
    return await _show_detail(update, context)


async def in_back_to_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'رجوع' target for any screen reached from the detail page (edit menu /
    visibility menu / results menu / delete-confirm) — always returns to
    that same initiative's detail page, never straight to the list or hub."""
    await update.callback_query.answer()
    return await _show_detail(update, context)


# ── Delete initiative ──────────────────────────────────────────────────────────

async def in_detail_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    initiative_id = context.user_data.get("in_initiative_id")
    initiative = ins.get_initiative(initiative_id)
    if not initiative:
        return await _show_list(update, context)
    await _reply(
        update,
        f"🗑 هل تريد حذف مبادرة *{initiative.get('name')}*؟\n\n"
        "سيتم حذف جميع طلباتها أيضاً.",
        _yn("in_delete_yes", "in_back_to_detail"),
    )
    return IN_DEL_CONFIRM


async def in_delete_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    initiative_id = context.user_data.get("in_initiative_id")
    ins.delete_initiative(initiative_id)
    await update.callback_query.answer("✅ تم حذف المبادرة.")
    return await _show_list(update, context)


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
    await update.message.reply_text("👥 كم العدد الأقصى للمقبولين في هذه المبادرة؟\n\nمثال: 1 أو 3 أو 5 أو 10")
    return IN_C_MAX


async def in_c_max(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم صحيح أكبر من أو يساوي 1.")
        return IN_C_MAX
    context.user_data["in_new"]["max_participants"] = int(text)
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
        max_participants=data.get("max_participants"),
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
        [InlineKeyboardButton("📝 تعديل الاسم", callback_data="in_e_name")],
        [InlineKeyboardButton("📄 تعديل الوصف", callback_data="in_e_desc")],
        [InlineKeyboardButton("🏆 تعديل عدد النقاط", callback_data="in_e_points")],
        [InlineKeyboardButton("👥 تعديل الحد الأقصى للمقبولين", callback_data="in_e_max")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="in_back_to_detail")],
    ])


async def in_detail_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "✏️ *تعديل المبادرة*\n\nاختر ما تريد تعديله:", _edit_menu_kb())
    return IN_EDIT_MENU


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


async def in_e_max(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "👥 أرسل الحد الأقصى الجديد للمقبولين في هذه المبادرة:")
    return IN_E_MAX


async def in_e_max_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم صحيح أكبر من أو يساوي 1.")
        return IN_E_MAX
    initiative_id = context.user_data.get("in_initiative_id")
    ins.update_initiative_field(initiative_id, max_participants=int(text))
    initiative = ins.get_initiative(initiative_id)
    # Existing executors are never removed when the cap is lowered below the
    # current count — new acceptances are just blocked until the count drops
    # under it. But if the cap is RAISED and that frees a seat on a locked
    # ('in_progress') initiative, reopen it to new requests automatically —
    # the same rule as excluding/cancelling an executor.
    if ins.get_initiative_status(initiative) == ins.INIT_STATUS_IN_PROGRESS \
            and ins.accepted_count(initiative_id) < int(text):
        ins.set_initiative_status(initiative_id, ins.INIT_STATUS_OPEN)
        initiative = ins.get_initiative(initiative_id)
    await update.message.reply_text(
        f"✅ تم تحديث الحد الأقصى.\n\nالحالة الآن: {ins.initiative_status_label(initiative)}",
        reply_markup=_edit_menu_kb(),
    )
    return IN_EDIT_MENU


# ── Show/hide initiative (confirm-first) ──────────────────────────────────────

def _vis_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 إظهار", callback_data="in_vis_show"),
         InlineKeyboardButton("🙈 إخفاء", callback_data="in_vis_hide")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="in_back_to_detail")],
    ])


async def in_detail_vis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    initiative_id = context.user_data.get("in_initiative_id")
    initiative = ins.get_initiative(initiative_id)
    if not initiative:
        return await _show_list(update, context)
    current = "✅ ظاهرة" if initiative.get("visible") else "🙈 مخفية"
    await _reply(update, f"👁 الحالة الحالية:\n\n*{current}*", _vis_menu_kb())
    return IN_VIS_MENU


async def in_vis_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    initiative_id = context.user_data.get("in_initiative_id")
    initiative = ins.get_initiative(initiative_id)
    if not initiative:
        return await _show_list(update, context)
    if initiative.get("visible"):
        await _reply(update, "ℹ️ المبادرة ظاهرة بالفعل.", _vis_menu_kb())
        return IN_VIS_MENU
    ins.set_initiative_visible(initiative_id, True)
    await _reply(update, "✅ تم إظهار المبادرة.\n\nالحالة الحالية:\n\n*✅ ظاهرة*", _vis_menu_kb())
    return IN_VIS_MENU


async def in_vis_hide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    initiative_id = context.user_data.get("in_initiative_id")
    initiative = ins.get_initiative(initiative_id)
    if not initiative:
        return await _show_list(update, context)
    if not initiative.get("visible"):
        await _reply(update, "ℹ️ المبادرة مخفية بالفعل.", _vis_menu_kb())
        return IN_VIS_MENU
    ins.set_initiative_visible(initiative_id, False)
    await _reply(update, "✅ تم إخفاء المبادرة.\n\nالحالة الحالية:\n\n*🙈 مخفية*", _vis_menu_kb())
    return IN_VIS_MENU


# ── Executors ("👥 المنفذون") ────────────────────────────────────────────────
#
# The participants an admin has chosen to actually carry out the initiative
# (status == accepted, not yet marked completed). An admin can exclude one
# of them at any time; if that frees a seat below the cap on an initiative
# that was locked ("in_progress"), it automatically reopens to new requests.

def _exec_list_kb(execs: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(r.get("user_name", "—"), callback_data=f"in_exec_{r['user_id']}")]
        for r in execs
    ]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="in_back_to_detail")])
    return InlineKeyboardMarkup(rows)


async def in_detail_execs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_exec_list(update, context)


async def _show_exec_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    initiative_id = context.user_data.get("in_initiative_id")
    execs = ins.executors_for_initiative(initiative_id)
    if not execs:
        await _reply(
            update, "📭 لا يوجد منفذون مختارون حالياً لهذه المبادرة.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="in_back_to_detail")]]),
        )
        return IN_EXEC_LIST
    await _reply(update, "👥 *المنفذون*\n\nاختر أحدهم:", _exec_list_kb(execs))
    return IN_EXEC_LIST


async def in_exec_back_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_exec_list(update, context)


async def in_exec_sel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.data.replace("in_exec_", "")
    initiative_id = context.user_data.get("in_initiative_id")
    context.user_data["in_exec_user_id"] = user_id

    r = ins.get_request(initiative_id, user_id)
    if not r:
        return await _show_exec_list(update, context)

    text = (
        f"👤 *{r.get('user_name', '—')}*\n"
        f"📅 وقت القبول: {_fmt_date(r.get('decided_at'))}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 استبعاد من المبادرة", callback_data="in_exec_exclude")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="in_exec_back_list")],
    ])
    await _reply(update, text, kb)
    return IN_EXEC_DETAIL


async def in_exec_exclude(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    initiative_id = context.user_data.get("in_initiative_id")
    user_id       = context.user_data.get("in_exec_user_id")
    r = ins.get_request(initiative_id, user_id)
    if not r:
        return await _show_exec_list(update, context)
    await _reply(
        update,
        f"🚫 هل تريد استبعاد *{r.get('user_name', '—')}* من تنفيذ هذه المبادرة؟\n\n"
        "لن يحصل على أي نقاط.",
        _yn("in_exec_exclude_yes", "in_exec_back_list"),
    )
    return IN_EXEC_DEL_CONFIRM


async def in_exec_exclude_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    initiative_id = context.user_data.get("in_initiative_id")
    user_id       = context.user_data.get("in_exec_user_id")
    reopened = ins.exclude_participant(initiative_id, user_id)

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text="تم استبعادك من تنفيذ المبادرة.",
        )
    except Exception as e:
        logger.warning("initiative exclude notify failed: %s", e)

    note = "\n\n🟢 المبادرة أصبحت مفتوحة لاستقبال طلبات جديدة." if reopened else ""
    await _reply(
        update, f"✅ تم استبعاد المتسابق وإشعاره.{note}",
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="in_back_to_detail")]]),
    )
    return IN_EXEC_LIST


# ── Results management ("🧹 إدارة النتائج") ─────────────────────────────────
#
# Three categories, scoped to the CURRENT initiative only:
#   ✔ النتائج المكتملة   — deleting one reverses its credited points
#   ❌ الطلبات المرفوضة   — deleting one is pure data cleanup (no balance effect)
#   🚫 الطلبات الملغاة    — same as rejected
#
# Deleting a request here removes it entirely, which also means it's no
# longer counted anywhere (accepted_count, "one active initiative" gate,
# etc.) — the participant is free to request again, per the requirement.

_RES_CATEGORIES = {
    "completed": (ins.STATUS_COMPLETED, "✔ النتائج المكتملة"),
    "rejected":  (ins.STATUS_REJECTED,  "❌ الطلبات المرفوضة"),
    "cancelled": (ins.STATUS_CANCELLED, "🚫 الطلبات الملغاة"),
    "excluded":  (ins.STATUS_EXCLUDED,  "🚫 المستبعدون"),
}


def _res_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✔ النتائج المكتملة", callback_data="in_res_cat_completed")],
        [InlineKeyboardButton("❌ الطلبات المرفوضة", callback_data="in_res_cat_rejected")],
        [InlineKeyboardButton("🚫 الطلبات الملغاة", callback_data="in_res_cat_cancelled")],
        [InlineKeyboardButton("🚫 المستبعدون", callback_data="in_res_cat_excluded")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="in_back_to_detail")],
    ])


async def in_detail_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "🧹 *إدارة النتائج*\n\nاختر الفئة:", _res_menu_kb())
    return IN_RES_MENU


def _res_list_kb(category: str, items: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"{r.get('user_name', '—')} — {_fmt_date(r.get('requested_at'))}",
                               callback_data=f"in_resitem_{r['user_id']}")]
        for r in items
    ]
    if items:
        rows.append([InlineKeyboardButton("🗑 حذف الكل", callback_data=f"in_res_delall_{category}")])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="in_detail_results")])
    return InlineKeyboardMarkup(rows)


async def _show_res_list(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str):
    initiative_id = context.user_data.get("in_initiative_id")
    status, label = _RES_CATEGORIES[category]
    context.user_data["in_res_status"] = category
    items = ins.requests_for_initiative(initiative_id, statuses=(status,))
    if not items:
        await _reply(
            update, f"📭 لا توجد عناصر في: {label}",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="in_detail_results")]]),
        )
        return IN_RES_LIST
    await _reply(update, f"{label}\n\nاختر عنصراً:", _res_list_kb(category, items))
    return IN_RES_LIST


async def in_res_cat_sel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category = query.data.replace("in_res_cat_", "")
    return await _show_res_list(update, context, category)


async def in_res_back_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    category = context.user_data.get("in_res_status", "completed")
    return await _show_res_list(update, context, category)


async def in_resitem_sel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.data.replace("in_resitem_", "")
    initiative_id = context.user_data.get("in_initiative_id")
    context.user_data["in_res_user_id"] = user_id

    r = ins.get_request(initiative_id, user_id)
    initiative = ins.get_initiative(initiative_id)
    category = context.user_data.get("in_res_status", "completed")
    if not r or not initiative:
        return await _show_res_list(update, context, category)

    status, label = _RES_CATEGORIES[category]
    lines = [
        f"{label}\n",
        f"👤 *{r.get('user_name', '—')}*",
        f"📅 وقت الطلب: {_fmt_date(r.get('requested_at'))}",
    ]
    if category == "completed":
        pts = r.get("points_awarded", initiative.get("points", 0))
        lines.append(f"🏆 النقاط الممنوحة: *{pts}*")
        del_label = "🗑 حذف النتيجة"
    else:
        del_label = "🗑 حذف الطلب"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(del_label, callback_data="in_resitem_delete")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="in_res_back_list")],
    ])
    await _reply(update, "\n".join(lines), kb)
    return IN_RES_DETAIL


async def in_resitem_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    initiative_id = context.user_data.get("in_initiative_id")
    user_id       = context.user_data.get("in_res_user_id")
    category      = context.user_data.get("in_res_status", "completed")
    initiative    = ins.get_initiative(initiative_id)
    r             = ins.get_request(initiative_id, user_id)

    if category == "completed" and r:
        pts = int(r.get("points_awarded", initiative.get("points", 0)) if initiative else r.get("points_awarded", 0))
        user_obj  = get_user(int(user_id)) or {}
        full_name = user_obj.get("full_name") or "—"
        bal_before = credits.get_balance(int(user_id))
        bal_after  = credits.add_credits(int(user_id), -pts)
        transactions.record(
            int(user_id), full_name, "initiative_result_remove", pts, bal_before, bal_after,
            f"حذف نتيجة مبادرة: {initiative.get('name', '—') if initiative else '—'}",
        )

    ins.delete_request(initiative_id, user_id)
    await update.callback_query.answer("✅ تم الحذف.")
    return await _show_res_list(update, context, category)


async def in_res_delall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category = query.data.replace("in_res_delall_", "")
    status, label = _RES_CATEGORIES.get(category, (None, ""))
    initiative_id = context.user_data.get("in_initiative_id")
    initiative = ins.get_initiative(initiative_id)

    removed = ins.delete_all_requests_by_status(initiative_id, status)

    if category == "completed":
        for r in removed:
            uid = r.get("user_id")
            pts = int(r.get("points_awarded", initiative.get("points", 0)) if initiative else r.get("points_awarded", 0))
            user_obj  = get_user(int(uid)) or {}
            full_name = user_obj.get("full_name") or "—"
            bal_before = credits.get_balance(int(uid))
            bal_after  = credits.add_credits(int(uid), -pts)
            transactions.record(
                int(uid), full_name, "initiative_result_remove", pts, bal_before, bal_after,
                f"حذف نتيجة مبادرة (حذف جماعي): {initiative.get('name', '—') if initiative else '—'}",
            )

    await _reply(
        update, f"✅ تم حذف *{len(removed)}* عنصر من: {label}",
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="in_detail_results")]]),
    )
    return IN_RES_LIST


# ── Execution requests ("📊 طلبات التنفيذ") — hub-level, all initiatives ────

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
    when = _fmt_date(r.get("requested_at"))
    return f"{r.get('user_name', '—')} — {name} — {when} — {status_label}"


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
    await _reply(update, "📊 *طلبات التنفيذ*\n\nمرتبة حسب وقت الطلب (الأقدم فالأحدث):", _req_list_kb(status))
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
        f"📅 وقت الطلب: {_fmt_date(r.get('requested_at'))}\n"
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
    initiative_id = context.user_data.get("in_req_initiative_id")
    user_id       = context.user_data.get("in_req_user_id")
    initiative    = ins.get_initiative(initiative_id)

    if not initiative or not ins.is_open_for_requests(initiative):
        await update.callback_query.answer("⚠️ هذه المبادرة لم تعد تستقبل قرارات جديدة.", show_alert=True)
        return IN_REQ_DETAIL

    await update.callback_query.answer()
    auto_rejected = ins.accept_participant(initiative_id, user_id)

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text="🎉 تم اختيارك لتنفيذ المبادرة.\n\nيرجى البدء في تنفيذها.",
        )
    except Exception as e:
        logger.warning("initiative accept notify failed: %s", e)

    try:
        import achievements_storage as ach
        ach.check_first_initiative_badge(user_id)
    except Exception as e:
        logger.warning("achievements first_initiative check failed: %s", e)

    # If this filled the last seat, every other still-pending requester for
    # this initiative was just auto-rejected — notify each of them.
    for r in auto_rejected:
        try:
            await context.bot.send_message(
                chat_id=int(r["user_id"]),
                text="نعتذر، تم اكتمال اختيار منفذي هذه المبادرة.",
            )
        except Exception as e:
            logger.warning("initiative auto-reject notify failed: %s", e)

    note = f"\n\n🟡 اكتمل اختيار المنفذين ({len(auto_rejected)} طلب تم رفضه تلقائياً)." if auto_rejected else ""
    await _reply(update, f"✅ تم قبول الطلب وإشعار المتسابق.{note}",
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
    reopened = ins.cancel_request(initiative_id, user_id)

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text="🚫 تم إلغاء المبادرة.",
        )
    except Exception as e:
        logger.warning("initiative cancel notify failed: %s", e)

    note = "\n\n🟢 المبادرة أصبحت مفتوحة لاستقبال طلبات جديدة." if reopened else ""
    await _reply(update, f"✅ تم إلغاء المبادرة وإشعار المتسابق.{note}",
                 InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="in_req_back")]]))
    return IN_REQ_DETAIL


async def in_req_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    initiative_id = context.user_data.get("in_req_initiative_id")
    user_id       = context.user_data.get("in_req_user_id")
    initiative    = ins.get_initiative(initiative_id)
    points        = int(initiative.get("points", 0)) if initiative else 0

    finished_all = ins.complete_participant(initiative_id, user_id, points)

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

    note = "\n\n✔ اكتمل تنفيذ جميع منفذي هذه المبادرة." if finished_all else ""
    await _reply(update, f"✅ تم احتساب *{points}* نقطة للمتسابق وإشعاره.{note}",
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

    return ConversationHandler(
        entry_points=[CallbackQueryHandler(in_hub, pattern="^adm_initiatives$")],
        states={
            IN_HUB: [
                CallbackQueryHandler(in_create,   pattern="^in_create$"),
                CallbackQueryHandler(in_list,     pattern="^in_list$"),
                CallbackQueryHandler(in_requests, pattern="^in_requests$"),
            ],
            IN_LIST: [
                CallbackQueryHandler(in_pick, pattern=r"^in_pick_\w+$"),
                hub_reentry,
            ],
            IN_DETAIL: [
                CallbackQueryHandler(in_detail_edit,    pattern="^in_detail_edit$"),
                CallbackQueryHandler(in_detail_vis,     pattern="^in_detail_vis$"),
                CallbackQueryHandler(in_detail_execs,   pattern="^in_detail_execs$"),
                CallbackQueryHandler(in_detail_results, pattern="^in_detail_results$"),
                CallbackQueryHandler(in_detail_delete,  pattern="^in_detail_delete$"),
                CallbackQueryHandler(in_back_to_list,   pattern="^in_back_to_list$"),
                hub_reentry,
            ],
            IN_DEL_CONFIRM: [
                CallbackQueryHandler(in_delete_yes,     pattern="^in_delete_yes$"),
                CallbackQueryHandler(in_back_to_detail, pattern="^in_back_to_detail$"),
                hub_reentry,
            ],
            IN_C_NAME:   [MessageHandler(filters.TEXT & ~filters.COMMAND, in_c_name), hub_reentry],
            IN_C_DESC:   [MessageHandler(filters.TEXT & ~filters.COMMAND, in_c_desc), hub_reentry],
            IN_C_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, in_c_points), hub_reentry],
            IN_C_MAX:    [MessageHandler(filters.TEXT & ~filters.COMMAND, in_c_max), hub_reentry],
            IN_C_VISIBLE: [
                CallbackQueryHandler(in_vis_yes, pattern="^in_vis_yes$"),
                CallbackQueryHandler(in_vis_no,  pattern="^in_vis_no$"),
                hub_reentry,
            ],
            IN_EDIT_MENU: [
                CallbackQueryHandler(in_e_name,         pattern="^in_e_name$"),
                CallbackQueryHandler(in_e_desc,         pattern="^in_e_desc$"),
                CallbackQueryHandler(in_e_points,       pattern="^in_e_points$"),
                CallbackQueryHandler(in_e_max,          pattern="^in_e_max$"),
                CallbackQueryHandler(in_back_to_detail, pattern="^in_back_to_detail$"),
                hub_reentry,
            ],
            IN_E_NAME:   [MessageHandler(filters.TEXT & ~filters.COMMAND, in_e_name_val), hub_reentry],
            IN_E_DESC:   [MessageHandler(filters.TEXT & ~filters.COMMAND, in_e_desc_val), hub_reentry],
            IN_E_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, in_e_points_val), hub_reentry],
            IN_E_MAX:    [MessageHandler(filters.TEXT & ~filters.COMMAND, in_e_max_val), hub_reentry],
            IN_VIS_MENU: [
                CallbackQueryHandler(in_vis_show,       pattern="^in_vis_show$"),
                CallbackQueryHandler(in_vis_hide,       pattern="^in_vis_hide$"),
                CallbackQueryHandler(in_back_to_detail, pattern="^in_back_to_detail$"),
                hub_reentry,
            ],
            IN_EXEC_LIST: [
                CallbackQueryHandler(in_exec_sel,       pattern=r"^in_exec_\w+$"),
                CallbackQueryHandler(in_back_to_detail, pattern="^in_back_to_detail$"),
                hub_reentry,
            ],
            IN_EXEC_DETAIL: [
                CallbackQueryHandler(in_exec_exclude,   pattern="^in_exec_exclude$"),
                CallbackQueryHandler(in_exec_back_list, pattern="^in_exec_back_list$"),
                hub_reentry,
            ],
            IN_EXEC_DEL_CONFIRM: [
                CallbackQueryHandler(in_exec_exclude_yes, pattern="^in_exec_exclude_yes$"),
                CallbackQueryHandler(in_exec_back_list,   pattern="^in_exec_back_list$"),
                hub_reentry,
            ],
            IN_RES_MENU: [
                CallbackQueryHandler(in_res_cat_sel,    pattern=r"^in_res_cat_\w+$"),
                CallbackQueryHandler(in_back_to_detail, pattern="^in_back_to_detail$"),
                hub_reentry,
            ],
            IN_RES_LIST: [
                CallbackQueryHandler(in_resitem_sel,    pattern=r"^in_resitem_\w+$"),
                CallbackQueryHandler(in_res_delall,     pattern=r"^in_res_delall_\w+$"),
                CallbackQueryHandler(in_detail_results, pattern="^in_detail_results$"),
                hub_reentry,
            ],
            IN_RES_DETAIL: [
                CallbackQueryHandler(in_resitem_delete, pattern="^in_resitem_delete$"),
                CallbackQueryHandler(in_res_back_list,  pattern="^in_res_back_list$"),
                hub_reentry,
            ],
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
