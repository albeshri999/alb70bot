# -*- coding: utf-8 -*-
"""
Admin side of the fully independent '🎭 إدارة المشاركات' (Submissions /
talent-contest) system.

Design notes (mirrors initiatives_admin.py's per-item detail/management page
style, for consistency with the rest of the bot):
- Its OWN ConversationHandler, entered from the admin main menu via the
  "adm_submissions" callback button (the only line added to admin.py).
- Hub: ➕ إنشاء مشاركة / 📋 قائمة المشاركات / 📥 المشاركات المرسلة /
  🏆 اعتماد النتائج / 🔙 رجوع.
- Picking a submission from the list opens its detail/management page
  (name, description, type, points, winners, max score, deadline, edit
  policy, visibility, entry count, finalized status) with buttons:
  ✏️ تعديل / 👁 إظهار-إخفاء / 🗑 حذف / 🔙 رجوع.
- "📥 المشاركات المرسلة" and "🏆 اعتماد النتائج" live at the HUB level (not
  inside each submission), exactly as requested — the former lists every
  uploaded entry across all contests (sorted by contest name then time),
  the latter lets the admin pick which contest to finalize.
- All data lives in submissions_storage.py (its own JSON files). Winner
  crediting goes through the existing credits.py/transactions.py helpers,
  exactly like the quiz/initiatives crediting flows — nothing here touches
  any other system's files. Badge awarding is delegated entirely to
  achievements_storage.award_submission_badges().
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
import submissions_storage as subs
import credits
import transactions

logger = logging.getLogger(__name__)

# ── States ────────────────────────────────────────────────────────────────────
(SB_HUB, SB_LIST, SB_DETAIL, SB_DEL_CONFIRM,
 SB_C_NAME, SB_C_DESC, SB_C_TYPE, SB_C_POINTS, SB_C_WINNERS, SB_C_MAXSCORE,
 SB_C_DEADLINE, SB_C_EDITABLE, SB_C_VISIBLE,
 SB_EDIT_MENU, SB_E_NAME, SB_E_DESC, SB_E_POINTS, SB_E_WINNERS, SB_E_MAXSCORE,
 SB_E_DEADLINE,
 SB_VIS_MENU,
 SB_ENTRIES_LIST, SB_ENTRY_DETAIL, SB_ENTRY_SCORE,
 SB_FINALIZE_LIST, SB_FINALIZE_CONFIRM,
 ) = range(26)


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
        [InlineKeyboardButton("➕ إنشاء مشاركة", callback_data="sb_create")],
        [InlineKeyboardButton("📋 قائمة المشاركات", callback_data="sb_list")],
        [InlineKeyboardButton("📥 المشاركات المرسلة", callback_data="sb_entries")],
        [InlineKeyboardButton("🏆 اعتماد النتائج", callback_data="sb_finalize")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="adm_main")],
    ])


async def sb_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer("⛔ ليس لديك صلاحية.", show_alert=True)
        return ConversationHandler.END
    if update.callback_query:
        await update.callback_query.answer()
    context.user_data.pop("sb_submission_id", None)
    await _reply(update, "🎭 *إدارة المشاركات*\n\nاختر العملية:", _hub_kb())
    return SB_HUB


# ── Submissions list ─────────────────────────────────────────────────────────

def _list_kb() -> InlineKeyboardMarkup:
    items = subs.load_submissions()
    rows = [
        [InlineKeyboardButton(v.get("name", "—"), callback_data=f"sb_pick_{k}")]
        for k, v in sorted(items.items(), key=lambda kv: int(kv[0]))
    ]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="sb_hub")])
    return InlineKeyboardMarkup(rows)


async def _show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = subs.load_submissions()
    if not items:
        await _reply(
            update, "📭 لا توجد مشاركات بعد.\n\nأنشئ مشاركة جديدة أولاً.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="sb_hub")]]),
        )
        return SB_LIST
    await _reply(update, "📋 *قائمة المشاركات*\n\nاختر مشاركة:", _list_kb())
    return SB_LIST


async def sb_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_list(update, context)


async def sb_back_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_list(update, context)


# ── Per-submission detail / management page ──────────────────────────────────

def _submission_detail_text(submission: dict) -> str:
    entries = subs.entries_for_submission(submission.get("id"))
    visibility = "✅ ظاهرة" if submission.get("visible") else "🙈 مخفية"
    editable = "✅ يسمح بالتعديل" if submission.get("allow_edit") else "❌ لا يسمح بالتعديل"
    finalized = "✔️ تم اعتماد النتائج" if submission.get("results_finalized") else "⏳ لم تُعتمد بعد"
    type_label = subs.MEDIA_TYPES.get(submission.get("media_type"), "—")
    return (
        f"🎭 *{submission.get('name')}*\n\n"
        f"📄 {submission.get('description') or '—'}\n\n"
        f"📎 نوع المشاركة: *{type_label}*\n"
        f"🏆 نقاط الفوز: *{submission.get('points', 0)}*\n"
        f"🔢 عدد الفائزين: *{submission.get('num_winners', 0)}*\n"
        f"💯 الدرجة العظمى: *{submission.get('max_score', 0)}*\n"
        f"⏰ آخر موعد: {_fmt_date(submission.get('deadline'))}\n"
        f"✏️ {editable}\n"
        f"👁 حالة الظهور: *{visibility}*\n"
        f"📥 عدد المشاركات المرسلة: *{len(entries)}*\n"
        f"🏁 حالة النتائج: {finalized}\n"
        f"📅 تاريخ الإنشاء: {_fmt_date(submission.get('created_at'))}"
    )


def _detail_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تعديل المشاركة", callback_data="sb_detail_edit")],
        [InlineKeyboardButton("👁 إظهار / إخفاء المشاركة", callback_data="sb_detail_vis")],
        [InlineKeyboardButton("🗑 حذف المشاركة", callback_data="sb_detail_delete")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="sb_back_to_list")],
    ])


async def _show_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    submission_id = context.user_data.get("sb_submission_id")
    submission = subs.get_submission(submission_id)
    if not submission:
        return await _show_list(update, context)
    await _reply(update, _submission_detail_text(submission), _detail_kb())
    return SB_DETAIL


async def sb_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    submission_id = query.data.replace("sb_pick_", "")
    context.user_data["sb_submission_id"] = submission_id
    return await _show_detail(update, context)


async def sb_back_to_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_detail(update, context)


# ── Delete submission ──────────────────────────────────────────────────────────

async def sb_detail_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    submission_id = context.user_data.get("sb_submission_id")
    submission = subs.get_submission(submission_id)
    if not submission:
        return await _show_list(update, context)
    await _reply(
        update,
        f"🗑 هل تريد حذف مشاركة *{submission.get('name')}*؟\n\n"
        "سيتم حذف جميع المرفقات المرسلة لها أيضاً.",
        _yn("sb_delete_yes", "sb_back_to_detail"),
    )
    return SB_DEL_CONFIRM


async def sb_delete_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    submission_id = context.user_data.get("sb_submission_id")
    subs.delete_submission(submission_id)
    await update.callback_query.answer("✅ تم حذف المشاركة.")
    return await _show_list(update, context)


# ── Create new submission ─────────────────────────────────────────────────────

async def sb_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["sb_new"] = {}
    await _reply(update, "🎭 أرسل *اسم المشاركة*:\n\nمثال: أفضل تلاوة القرآن")
    return SB_C_NAME


async def sb_c_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if len(text) < 2:
        await update.message.reply_text("⚠️ الرجاء إدخال اسم صحيح.")
        return SB_C_NAME
    context.user_data["sb_new"]["name"] = text
    await update.message.reply_text("📄 أرسل *وصف المشاركة*:")
    return SB_C_DESC


async def sb_c_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["sb_new"]["description"] = "" if text == "-" else text
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎤 تسجيل صوتي", callback_data="sb_type_audio")],
        [InlineKeyboardButton("🎥 فيديو", callback_data="sb_type_video")],
        [InlineKeyboardButton("📷 صورة", callback_data="sb_type_photo")],
    ])
    await update.message.reply_text("📎 اختر *نوع المشاركة*:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    return SB_C_TYPE


async def sb_c_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    media_type = query.data.replace("sb_type_", "")
    context.user_data["sb_new"]["media_type"] = media_type
    await _reply(update, "🏆 أرسل *عدد النقاط* التي يحصل عليها الفائز:")
    return SB_C_POINTS


async def sb_c_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم صحيح أكبر من صفر.")
        return SB_C_POINTS
    context.user_data["sb_new"]["points"] = int(text)
    await update.message.reply_text("🔢 أرسل *عدد الفائزين*:\n\nمثال: 1 أو 2 أو 3")
    return SB_C_WINNERS


async def sb_c_winners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم صحيح أكبر من صفر.")
        return SB_C_WINNERS
    context.user_data["sb_new"]["num_winners"] = int(text)
    await update.message.reply_text("💯 أرسل *الدرجة العظمى* للتقييم:\n\nمثال: 100 أو 50 أو 20")
    return SB_C_MAXSCORE


async def sb_c_maxscore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم صحيح أكبر من صفر.")
        return SB_C_MAXSCORE
    context.user_data["sb_new"]["max_score"] = int(text)
    await update.message.reply_text(
        "⏰ أرسل *آخر موعد لاستقبال المشاركات*:\n\n"
        "بصيغة: YYYY-MM-DD HH:MM\n"
        "مثال: 2026-07-20 18:00"
    )
    return SB_C_DEADLINE


async def sb_c_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    iso = subs.parse_deadline_text(text)
    if not iso:
        await update.message.reply_text(
            "⚠️ صيغة غير صحيحة. الرجاء إرسال التاريخ بصيغة: YYYY-MM-DD HH:MM\nمثال: 2026-07-20 18:00"
        )
        return SB_C_DEADLINE
    context.user_data["sb_new"]["deadline"] = iso
    await update.message.reply_text(
        "✏️ هل يسمح للمتسابق بتعديل مشاركته قبل انتهاء الموعد؟",
        reply_markup=_yn("sb_editable_yes", "sb_editable_no"),
    )
    return SB_C_EDITABLE


async def sb_editable_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["sb_new"]["allow_edit"] = True
    await _reply(update, "👁 هل المشاركة ظاهرة للمتسابقين؟", _yn("sb_vis_yes", "sb_vis_no"))
    return SB_C_VISIBLE


async def sb_editable_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["sb_new"]["allow_edit"] = False
    await _reply(update, "👁 هل المشاركة ظاهرة للمتسابقين؟", _yn("sb_vis_yes", "sb_vis_no"))
    return SB_C_VISIBLE


async def _finish_create(update: Update, context: ContextTypes.DEFAULT_TYPE, visible: bool):
    data = context.user_data.get("sb_new", {})
    subs.create_submission(
        name=data.get("name", "بدون اسم"),
        description=data.get("description", ""),
        media_type=data.get("media_type", "audio"),
        points=data.get("points", 0),
        num_winners=data.get("num_winners", 1),
        max_score=data.get("max_score", 100),
        deadline_iso=data.get("deadline"),
        allow_edit=data.get("allow_edit", False),
        visible=visible,
    )
    context.user_data.pop("sb_new", None)
    await _reply(update, "✅ تم إنشاء المشاركة بنجاح.", _hub_kb())
    return SB_HUB


async def sb_vis_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _finish_create(update, context, True)


async def sb_vis_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _finish_create(update, context, False)


# ── Edit existing submission ──────────────────────────────────────────────────

def _edit_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 تعديل الاسم", callback_data="sb_e_name")],
        [InlineKeyboardButton("📄 تعديل الوصف", callback_data="sb_e_desc")],
        [InlineKeyboardButton("🏆 تعديل عدد النقاط", callback_data="sb_e_points")],
        [InlineKeyboardButton("🔢 تعديل عدد الفائزين", callback_data="sb_e_winners")],
        [InlineKeyboardButton("💯 تعديل الدرجة العظمى", callback_data="sb_e_maxscore")],
        [InlineKeyboardButton("⏰ تعديل آخر موعد", callback_data="sb_e_deadline")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="sb_back_to_detail")],
    ])


async def sb_detail_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "✏️ *تعديل المشاركة*\n\nاختر ما تريد تعديله:", _edit_menu_kb())
    return SB_EDIT_MENU


async def sb_e_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "📝 أرسل الاسم الجديد:")
    return SB_E_NAME


async def sb_e_name_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if len(text) < 2:
        await update.message.reply_text("⚠️ الرجاء إدخال اسم صحيح.")
        return SB_E_NAME
    subs.update_submission_field(context.user_data.get("sb_submission_id"), name=text)
    await update.message.reply_text("✅ تم تحديث الاسم.", reply_markup=_edit_menu_kb())
    return SB_EDIT_MENU


async def sb_e_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "📄 أرسل الوصف الجديد (أرسل `-` لإفراغه):")
    return SB_E_DESC


async def sb_e_desc_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    subs.update_submission_field(context.user_data.get("sb_submission_id"),
                                  description="" if text == "-" else text)
    await update.message.reply_text("✅ تم تحديث الوصف.", reply_markup=_edit_menu_kb())
    return SB_EDIT_MENU


async def sb_e_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "🏆 أرسل عدد النقاط الجديد:")
    return SB_E_POINTS


async def sb_e_points_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم صحيح أكبر من صفر.")
        return SB_E_POINTS
    subs.update_submission_field(context.user_data.get("sb_submission_id"), points=int(text))
    await update.message.reply_text("✅ تم تحديث النقاط.", reply_markup=_edit_menu_kb())
    return SB_EDIT_MENU


async def sb_e_winners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "🔢 أرسل عدد الفائزين الجديد:")
    return SB_E_WINNERS


async def sb_e_winners_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم صحيح أكبر من صفر.")
        return SB_E_WINNERS
    subs.update_submission_field(context.user_data.get("sb_submission_id"), num_winners=int(text))
    await update.message.reply_text("✅ تم تحديث عدد الفائزين.", reply_markup=_edit_menu_kb())
    return SB_EDIT_MENU


async def sb_e_maxscore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "💯 أرسل الدرجة العظمى الجديدة:")
    return SB_E_MAXSCORE


async def sb_e_maxscore_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم صحيح أكبر من صفر.")
        return SB_E_MAXSCORE
    subs.update_submission_field(context.user_data.get("sb_submission_id"), max_score=int(text))
    await update.message.reply_text("✅ تم تحديث الدرجة العظمى.", reply_markup=_edit_menu_kb())
    return SB_EDIT_MENU


async def sb_e_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "⏰ أرسل آخر موعد جديد بصيغة: YYYY-MM-DD HH:MM")
    return SB_E_DEADLINE


async def sb_e_deadline_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    iso = subs.parse_deadline_text(text)
    if not iso:
        await update.message.reply_text("⚠️ صيغة غير صحيحة. الرجاء إرسال التاريخ بصيغة: YYYY-MM-DD HH:MM")
        return SB_E_DEADLINE
    subs.update_submission_field(context.user_data.get("sb_submission_id"), deadline=iso)
    await update.message.reply_text("✅ تم تحديث الموعد النهائي.", reply_markup=_edit_menu_kb())
    return SB_EDIT_MENU


# ── Show/hide submission (confirm-first) ──────────────────────────────────────

def _vis_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 إظهار", callback_data="sb_vis_show"),
         InlineKeyboardButton("🙈 إخفاء", callback_data="sb_vis_hide")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="sb_back_to_detail")],
    ])


async def sb_detail_vis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    submission_id = context.user_data.get("sb_submission_id")
    submission = subs.get_submission(submission_id)
    if not submission:
        return await _show_list(update, context)
    current = "✅ ظاهرة" if submission.get("visible") else "🙈 مخفية"
    await _reply(update, f"👁 الحالة الحالية:\n\n*{current}*", _vis_menu_kb())
    return SB_VIS_MENU


async def sb_vis_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    submission_id = context.user_data.get("sb_submission_id")
    submission = subs.get_submission(submission_id)
    if not submission:
        return await _show_list(update, context)
    if submission.get("visible"):
        await _reply(update, "ℹ️ المشاركة ظاهرة بالفعل.", _vis_menu_kb())
        return SB_VIS_MENU
    subs.set_submission_visible(submission_id, True)
    await _reply(update, "✅ تم إظهار المشاركة.\n\nالحالة الحالية:\n\n*✅ ظاهرة*", _vis_menu_kb())
    return SB_VIS_MENU


async def sb_vis_hide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    submission_id = context.user_data.get("sb_submission_id")
    submission = subs.get_submission(submission_id)
    if not submission:
        return await _show_list(update, context)
    if not submission.get("visible"):
        await _reply(update, "ℹ️ المشاركة مخفية بالفعل.", _vis_menu_kb())
        return SB_VIS_MENU
    subs.set_submission_visible(submission_id, False)
    await _reply(update, "✅ تم إخفاء المشاركة.\n\nالحالة الحالية:\n\n*🙈 مخفية*", _vis_menu_kb())
    return SB_VIS_MENU


# ── Entries ("📥 المشاركات المرسلة") — hub-level, all submissions ──────────

def _entry_status_label(entry: dict) -> str:
    status = entry.get("status")
    if status == subs.ENTRY_STATUS_WINNER:
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(entry.get("rank"), "🏆")
        return f"{medal} فائز (المركز {entry.get('rank')})"
    if status == subs.ENTRY_STATUS_PARTICIPANT:
        return "🔹 مشارك"
    if entry.get("score") is not None:
        return f"📝 تم التقييم ({entry.get('score')})"
    return "📥 مرسلة (بانتظار التقييم)"


def _all_entries_sorted() -> list:
    """Every entry across all submissions, sorted by submission name then
    submission time — exactly as requested."""
    submissions = subs.load_submissions()

    def _key(e):
        name = submissions.get(str(e.get("submission_id")), {}).get("name", "")
        return (name, e.get("submitted_at", ""))

    return sorted(subs.all_entries(), key=_key)


def _entries_list_kb(entries: list) -> InlineKeyboardMarkup:
    submissions = subs.load_submissions()
    rows = []
    for e in entries:
        sub_name = submissions.get(str(e.get("submission_id")), {}).get("name", "—")
        label = f"{sub_name} — {e.get('user_name', '—')} — {_fmt_date(e.get('submitted_at'))}"
        rows.append([InlineKeyboardButton(
            label[:64], callback_data=f"sb_entry_{e['submission_id']}_{e['user_id']}"
        )])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="sb_hub")])
    return InlineKeyboardMarkup(rows)


async def _show_entries_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    entries = _all_entries_sorted()
    if not entries:
        await _reply(
            update, "📭 لا توجد مشاركات مرسلة بعد.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="sb_hub")]]),
        )
        return SB_ENTRIES_LIST
    await _reply(update, "📥 *المشاركات المرسلة*\n\nاختر مشاركة:", _entries_list_kb(entries))
    return SB_ENTRIES_LIST


async def sb_entries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_entries_list(update, context)


async def sb_entries_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_entries_list(update, context)


async def sb_entry_sel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    payload = query.data.replace("sb_entry_", "")
    submission_id, user_id = payload.split("_", 1)
    context.user_data["sb_entry_submission_id"] = submission_id
    context.user_data["sb_entry_user_id"] = user_id

    entry = subs.get_entry(submission_id, user_id)
    submission = subs.get_submission(submission_id)
    if not entry or not submission:
        return await _show_entries_list(update, context)

    text = (
        f"🎭 المشاركة: *{submission.get('name')}*\n"
        f"👤 المتسابق: *{entry.get('user_name', '—')}*\n"
        f"📅 وقت الإرسال: {_fmt_date(entry.get('submitted_at'))}\n"
        f"📎 نوع المرفق: *{subs.MEDIA_TYPES.get(entry.get('file_type'), '—')}*\n"
        f"الحالة: {_entry_status_label(entry)}\n"
        f"💯 الدرجة الحالية: *{entry.get('score') if entry.get('score') is not None else '—'}* "
        f"من *{submission.get('max_score', 0)}*"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📎 عرض المرفق", callback_data="sb_entry_view_file")],
        [InlineKeyboardButton("📝 إعطاء درجة", callback_data="sb_entry_score")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="sb_entries_back")],
    ])
    await _reply(update, text, kb)
    return SB_ENTRY_DETAIL


async def sb_entry_view_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    submission_id = context.user_data.get("sb_entry_submission_id")
    user_id       = context.user_data.get("sb_entry_user_id")
    entry = subs.get_entry(submission_id, user_id)
    if not entry:
        return SB_ENTRY_DETAIL
    file_id   = entry.get("file_id")
    file_type = entry.get("file_type")
    try:
        if file_type == "audio":
            await query.message.reply_voice(file_id)
        elif file_type == "video":
            await query.message.reply_video(file_id)
        elif file_type == "photo":
            await query.message.reply_photo(file_id)
    except Exception as e:
        logger.warning("sb_entry_view_file failed: %s", e)
        await query.message.reply_text("⚠️ تعذر عرض المرفق.")
    return SB_ENTRY_DETAIL


async def sb_entry_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    submission_id = context.user_data.get("sb_entry_submission_id")
    submission = subs.get_submission(submission_id)
    max_score = submission.get("max_score", 100) if submission else 100
    await _reply(update, f"📝 أرسل الدرجة (من *{max_score}*):")
    return SB_ENTRY_SCORE


async def sb_entry_score_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    submission_id = context.user_data.get("sb_entry_submission_id")
    user_id       = context.user_data.get("sb_entry_user_id")
    submission    = subs.get_submission(submission_id)
    max_score     = submission.get("max_score", 100) if submission else 100

    try:
        score = float(text)
    except ValueError:
        await update.message.reply_text(f"⚠️ الرجاء إدخال رقم صحيح بين 0 و {max_score}.")
        return SB_ENTRY_SCORE
    if score < 0 or score > max_score:
        await update.message.reply_text(f"⚠️ الدرجة يجب أن تكون بين 0 و {max_score}.")
        return SB_ENTRY_SCORE
    if score == int(score):
        score = int(score)

    subs.set_entry_score(submission_id, user_id, score)
    entry = subs.get_entry(submission_id, user_id)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📎 عرض المرفق", callback_data="sb_entry_view_file")],
        [InlineKeyboardButton("📝 إعطاء درجة", callback_data="sb_entry_score")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="sb_entries_back")],
    ])
    await update.message.reply_text(
        f"✅ تم تسجيل الدرجة: *{score}* من *{max_score}*",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb,
    )
    return SB_ENTRY_DETAIL


# ── Finalize results ("🏆 اعتماد النتائج") ──────────────────────────────────

def _finalizable_submissions() -> dict:
    items = subs.load_submissions()
    out = {}
    for k, v in items.items():
        if v.get("results_finalized"):
            continue
        if subs.entries_for_submission(k):
            out[k] = v
    return out


def _finalize_list_kb() -> InlineKeyboardMarkup:
    items = _finalizable_submissions()
    rows = [
        [InlineKeyboardButton(v.get("name", "—"), callback_data=f"sb_fin_pick_{k}")]
        for k, v in sorted(items.items(), key=lambda kv: int(kv[0]))
    ]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="sb_hub")])
    return InlineKeyboardMarkup(rows)


async def _show_finalize_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _finalizable_submissions():
        await _reply(
            update, "📭 لا توجد مشاركات جاهزة لاعتماد نتائجها حالياً.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="sb_hub")]]),
        )
        return SB_FINALIZE_LIST
    await _reply(update, "🏆 *اعتماد النتائج*\n\nاختر مشاركة لاعتماد نتائجها:", _finalize_list_kb())
    return SB_FINALIZE_LIST


async def sb_finalize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_finalize_list(update, context)


async def sb_fin_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    submission_id = query.data.replace("sb_fin_pick_", "")
    context.user_data["sb_finalize_id"] = submission_id
    submission = subs.get_submission(submission_id)
    if not submission:
        return await _show_finalize_list(update, context)

    entries = subs.entries_for_submission(submission_id)
    scored = sum(1 for e in entries if e.get("score") is not None)
    await _reply(
        update,
        f"🏆 اعتماد نتائج: *{submission.get('name')}*\n\n"
        f"📥 عدد المشاركات: *{len(entries)}*\n"
        f"📝 تم تقييم: *{scored}*\n"
        f"🔢 عدد الفائزين المطلوب: *{submission.get('num_winners', 0)}*\n\n"
        "هل تريد اعتماد النتائج الآن؟ لا يمكن التراجع عن هذا الإجراء.",
        _yn("sb_fin_confirm_yes", "sb_hub"),
    )
    return SB_FINALIZE_CONFIRM


async def sb_fin_confirm_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    submission_id = context.user_data.get("sb_finalize_id")
    submission = subs.get_submission(submission_id)
    if not submission:
        context.user_data.pop("sb_submission_id", None)
        await _reply(update, "🎭 *إدارة المشاركات*\n\nاختر العملية:", _hub_kb())
        return SB_HUB

    winners, others = subs.finalize_submission(submission_id)
    points = int(submission.get("points", 0))
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}

    for w in winners:
        uid = int(w["user_id"])
        user_obj  = get_user(uid) or {}
        full_name = user_obj.get("full_name") or "—"
        bal_before = credits.get_balance(uid)
        bal_after  = credits.add_credits(uid, points)
        transactions.record(
            uid, full_name, "submission_win", points, bal_before, bal_after,
            f"فوز في مشاركة: {submission.get('name', '—')}",
        )
        rank_icon = medal.get(w.get("rank"), "🏆")
        rank_word = {1: "الأول", 2: "الثاني", 3: "الثالث"}.get(w.get("rank"), str(w.get("rank")))
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    f"{rank_icon} مبارك، حصلت على المركز {rank_word} في {submission.get('name')}.\n\n"
                    f"🏆 تمت إضافة نقاط الفوز ({points}) إلى رصيدك."
                ),
            )
        except Exception as e:
            logger.warning("submission winner notify failed: %s", e)

    for o in others:
        uid = int(o["user_id"])
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"🙏 شكراً لمشاركتك في {submission.get('name')}.",
            )
        except Exception as e:
            logger.warning("submission thanks notify failed: %s", e)

    try:
        import achievements_storage as ach
        ach.award_submission_badges(submission_id, submission.get("name", "—"), winners)
    except Exception as e:
        logger.warning("achievements submission badge award failed: %s", e)

    await _reply(
        update,
        f"✅ تم اعتماد النتائج.\n\n🏆 عدد الفائزين: *{len(winners)}*\n🔹 عدد المشاركين الآخرين: *{len(others)}*",
        _hub_kb(),
    )
    return SB_HUB


# ── Cancel fallback ───────────────────────────────────────────────────────────

async def sb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END


# ── Build the ConversationHandler ─────────────────────────────────────────────

def build_submissions_admin_handler() -> ConversationHandler:
    hub_reentry = CallbackQueryHandler(sb_hub, pattern="^sb_hub$")

    return ConversationHandler(
        entry_points=[CallbackQueryHandler(sb_hub, pattern="^adm_submissions$")],
        states={
            SB_HUB: [
                CallbackQueryHandler(sb_create,   pattern="^sb_create$"),
                CallbackQueryHandler(sb_list,     pattern="^sb_list$"),
                CallbackQueryHandler(sb_entries,  pattern="^sb_entries$"),
                CallbackQueryHandler(sb_finalize, pattern="^sb_finalize$"),
            ],
            SB_LIST: [
                CallbackQueryHandler(sb_pick, pattern=r"^sb_pick_\w+$"),
                hub_reentry,
            ],
            SB_DETAIL: [
                CallbackQueryHandler(sb_detail_edit,   pattern="^sb_detail_edit$"),
                CallbackQueryHandler(sb_detail_vis,    pattern="^sb_detail_vis$"),
                CallbackQueryHandler(sb_detail_delete, pattern="^sb_detail_delete$"),
                CallbackQueryHandler(sb_back_to_list,  pattern="^sb_back_to_list$"),
                hub_reentry,
            ],
            SB_DEL_CONFIRM: [
                CallbackQueryHandler(sb_delete_yes,     pattern="^sb_delete_yes$"),
                CallbackQueryHandler(sb_back_to_detail, pattern="^sb_back_to_detail$"),
                hub_reentry,
            ],
            SB_C_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, sb_c_name), hub_reentry],
            SB_C_DESC:     [MessageHandler(filters.TEXT & ~filters.COMMAND, sb_c_desc), hub_reentry],
            SB_C_TYPE: [
                CallbackQueryHandler(sb_c_type, pattern=r"^sb_type_(audio|video|photo)$"),
                hub_reentry,
            ],
            SB_C_POINTS:   [MessageHandler(filters.TEXT & ~filters.COMMAND, sb_c_points), hub_reentry],
            SB_C_WINNERS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, sb_c_winners), hub_reentry],
            SB_C_MAXSCORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, sb_c_maxscore), hub_reentry],
            SB_C_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, sb_c_deadline), hub_reentry],
            SB_C_EDITABLE: [
                CallbackQueryHandler(sb_editable_yes, pattern="^sb_editable_yes$"),
                CallbackQueryHandler(sb_editable_no,  pattern="^sb_editable_no$"),
                hub_reentry,
            ],
            SB_C_VISIBLE: [
                CallbackQueryHandler(sb_vis_yes, pattern="^sb_vis_yes$"),
                CallbackQueryHandler(sb_vis_no,  pattern="^sb_vis_no$"),
                hub_reentry,
            ],
            SB_EDIT_MENU: [
                CallbackQueryHandler(sb_e_name,         pattern="^sb_e_name$"),
                CallbackQueryHandler(sb_e_desc,         pattern="^sb_e_desc$"),
                CallbackQueryHandler(sb_e_points,       pattern="^sb_e_points$"),
                CallbackQueryHandler(sb_e_winners,      pattern="^sb_e_winners$"),
                CallbackQueryHandler(sb_e_maxscore,     pattern="^sb_e_maxscore$"),
                CallbackQueryHandler(sb_e_deadline,     pattern="^sb_e_deadline$"),
                CallbackQueryHandler(sb_back_to_detail, pattern="^sb_back_to_detail$"),
                hub_reentry,
            ],
            SB_E_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, sb_e_name_val), hub_reentry],
            SB_E_DESC:     [MessageHandler(filters.TEXT & ~filters.COMMAND, sb_e_desc_val), hub_reentry],
            SB_E_POINTS:   [MessageHandler(filters.TEXT & ~filters.COMMAND, sb_e_points_val), hub_reentry],
            SB_E_WINNERS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, sb_e_winners_val), hub_reentry],
            SB_E_MAXSCORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, sb_e_maxscore_val), hub_reentry],
            SB_E_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, sb_e_deadline_val), hub_reentry],
            SB_VIS_MENU: [
                CallbackQueryHandler(sb_vis_show,       pattern="^sb_vis_show$"),
                CallbackQueryHandler(sb_vis_hide,       pattern="^sb_vis_hide$"),
                CallbackQueryHandler(sb_back_to_detail, pattern="^sb_back_to_detail$"),
                hub_reentry,
            ],
            SB_ENTRIES_LIST: [
                CallbackQueryHandler(sb_entry_sel, pattern=r"^sb_entry_\w+_\d+$"),
                hub_reentry,
            ],
            SB_ENTRY_DETAIL: [
                CallbackQueryHandler(sb_entry_view_file, pattern="^sb_entry_view_file$"),
                CallbackQueryHandler(sb_entry_score,     pattern="^sb_entry_score$"),
                CallbackQueryHandler(sb_entries_back,    pattern="^sb_entries_back$"),
                hub_reentry,
            ],
            SB_ENTRY_SCORE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, sb_entry_score_val),
                hub_reentry,
            ],
            SB_FINALIZE_LIST: [
                CallbackQueryHandler(sb_fin_pick, pattern=r"^sb_fin_pick_\w+$"),
                hub_reentry,
            ],
            SB_FINALIZE_CONFIRM: [
                CallbackQueryHandler(sb_fin_confirm_yes, pattern="^sb_fin_confirm_yes$"),
                hub_reentry,
            ],
        },
        fallbacks=[MessageHandler(filters.COMMAND, sb_cancel)],
        name="submissions_admin_conv",
        persistent=False,
        allow_reentry=True,
    )
