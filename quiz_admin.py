# -*- coding: utf-8 -*-
"""
Admin side of the new, fully independent '📝 إدارة الاختبارات' (Quiz Management)
system.

Design notes (why this is safe to bolt onto the existing bot):
- This is its OWN ConversationHandler, entirely separate from the one in
  admin.py. It is entered from the admin main menu via the "adm_quizzes"
  callback button (the only line added to admin.py).
- Because python-telegram-bot's ConversationHandler simply ignores updates
  that don't match its current state (instead of consuming them), pressing
  "⬅️ القائمة الرئيسية" (callback_data="adm_main") from anywhere in this
  conversation is NOT handled here — it falls through untouched to the
  original admin ConversationHandler (still parked in its MAIN state),
  which shows the normal admin main menu exactly as before.
- All data lives in quiz_storage.py (its own JSON files). Nothing here reads
  or writes days.json, users.json, config.json, credit_log.json,
  transactions.json, admin_log.json, or recharge_codes.json.
"""
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters,
)
from telegram.constants import ParseMode

from config import ADMIN_ID
from storage import get_user
import quiz_storage as qs

logger = logging.getLogger(__name__)

# ── States ────────────────────────────────────────────────────────────────────
(QA_HUB, QA_LIST, QA_QUIZ_MENU, QA_DEL_CONFIRM,
 QA_C_NAME, QA_C_DESC, QA_C_POINTS, QA_C_TIMED, QA_C_MINUTES, QA_C_VISIBLE,
 QA_C_Q_TEXT, QA_C_Q_OPT_A, QA_C_Q_OPT_B, QA_C_Q_CORRECT, QA_C_Q_MORE,
 QA_EDIT_MENU, QA_E_NAME, QA_E_DESC, QA_E_POINTS, QA_E_TIMED, QA_E_MINUTES,
 QA_E_Q_LIST, QA_E_Q_FIELD_MENU, QA_E_Q_FIELD_VAL, QA_E_Q_DEL_CONFIRM,
 ) = range(25)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_admin(uid: int) -> bool:
    return uid == ADMIN_ID


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


def _yn(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ نعم", callback_data=yes_cb),
        InlineKeyboardButton("❌ لا", callback_data=no_cb),
    ]])


# ── Hub ───────────────────────────────────────────────────────────────────────

def _hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إنشاء اختبار جديد", callback_data="qa_create")],
        [InlineKeyboardButton("📋 قائمة الاختبارات", callback_data="qa_list_menu")],
        [InlineKeyboardButton("✏️ تعديل اختبار", callback_data="qa_list_edit")],
        [InlineKeyboardButton("🗑 حذف اختبار", callback_data="qa_list_delete")],
        [InlineKeyboardButton("👁 إظهار / إخفاء اختبار", callback_data="qa_list_toggle")],
        [InlineKeyboardButton("📊 نتائج الاختبارات", callback_data="qa_list_results")],
        [InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="adm_main")],
    ])


async def qa_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
    context.user_data.pop("qa_quiz_id", None)
    context.user_data.pop("qa_action", None)
    await _reply(update, "📝 *إدارة الاختبارات*\n\nاختر العملية:", _hub_kb())
    return QA_HUB


# ── Quiz list (shared by list/edit/delete/toggle/results actions) ────────────

def _quiz_list_kb() -> InlineKeyboardMarkup:
    quizzes = qs.load_quizzes()
    rows = [
        [InlineKeyboardButton(q.get("name", "—"), callback_data=f"qa_pick_{qid}")]
        for qid, q in sorted(quizzes.items(), key=lambda kv: int(kv[0]))
    ]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="qa_hub")])
    return InlineKeyboardMarkup(rows)


async def qa_list_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    action = query.data.replace("qa_list_", "")  # menu | edit | delete | toggle | results
    context.user_data["qa_action"] = action

    quizzes = qs.load_quizzes()
    if not quizzes:
        await _reply(
            update, "📭 لا توجد اختبارات بعد.\n\nأنشئ اختباراً جديداً أولاً.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="qa_hub")]]),
        )
        return QA_LIST

    await _reply(update, "📋 *قائمة الاختبارات*\n\nاختر اختباراً:", _quiz_list_kb())
    return QA_LIST


async def qa_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    quiz_id = query.data.replace("qa_pick_", "")
    context.user_data["qa_quiz_id"] = quiz_id
    action = context.user_data.get("qa_action", "menu")

    if action == "delete":
        return await _show_delete_confirm(update, context)
    if action == "results":
        return await _show_results(update, context)
    return await _show_quiz_menu(update, context)


# ── Per-quiz menu ─────────────────────────────────────────────────────────────

def _quiz_menu_kb(quiz: dict) -> InlineKeyboardMarkup:
    vis    = "👁 إخفاء الاختبار" if quiz.get("visible") else "👁 إظهار الاختبار"
    score  = "🙈 إخفاء الدرجة عن المتسابق" if quiz.get("show_score", True) else "👁 إظهار الدرجة للمتسابق"
    retake = "🔁 منع إعادة الاختبار" if quiz.get("allow_retake") else "🔁 السماح بإعادة الاختبار"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تعديل", callback_data="qa_edit_menu")],
        [InlineKeyboardButton(vis, callback_data="qa_toggle_visible")],
        [InlineKeyboardButton(score, callback_data="qa_toggle_score")],
        [InlineKeyboardButton(retake, callback_data="qa_toggle_retake")],
        [InlineKeyboardButton("📊 النتائج", callback_data="qa_show_results")],
        [InlineKeyboardButton("🗑 حذف", callback_data="qa_delete_confirm")],
        [InlineKeyboardButton("🔙 رجوع لقائمة الاختبارات", callback_data="qa_list_menu")],
        [InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="adm_main")],
    ])


def _quiz_summary(quiz: dict) -> str:
    n_q   = len(quiz.get("questions", []))
    total = n_q * int(quiz.get("points_per_question", 0))
    time_line = f"{quiz.get('time_minutes')} دقيقة" if quiz.get("timed") else "بدون تحديد"
    lines = [
        f"📝 *{_md(quiz.get('name', '—'))}*",
    ]
    if quiz.get("description"):
        lines.append(f"_{_md(quiz['description'])}_")
    lines += [
        "",
        f"❓ عدد الأسئلة: *{n_q}*",
        f"🔢 درجة كل سؤال: *{quiz.get('points_per_question', 0)}*  (الإجمالي: *{total}*)",
        f"⏱ الوقت: *{time_line}*",
        f"👁 ظاهر للمتسابقين: *{'نعم' if quiz.get('visible') else 'لا'}*",
        f"📊 إظهار الدرجة للمتسابق: *{'نعم' if quiz.get('show_score', True) else 'لا'}*",
        f"🔁 السماح بالإعادة: *{'نعم' if quiz.get('allow_retake') else 'لا'}*",
    ]
    return "\n".join(lines)


async def _show_quiz_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz_id = context.user_data.get("qa_quiz_id")
    quiz    = qs.get_quiz(quiz_id)
    if not quiz:
        await _reply(update, "⚠️ لم يعد هذا الاختبار موجوداً.", _hub_kb())
        return QA_HUB
    await _reply(update, _quiz_summary(quiz) + "\n\nاختر العملية:", _quiz_menu_kb(quiz))
    return QA_QUIZ_MENU


async def qa_back_to_quiz_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_quiz_menu(update, context)


async def qa_toggle_visible(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    quiz_id = context.user_data.get("qa_quiz_id")
    quiz    = qs.get_quiz(quiz_id)
    qs.update_quiz_field(quiz_id, visible=not quiz.get("visible"))
    return await _show_quiz_menu(update, context)


async def qa_toggle_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    quiz_id = context.user_data.get("qa_quiz_id")
    quiz    = qs.get_quiz(quiz_id)
    qs.update_quiz_field(quiz_id, show_score=not quiz.get("show_score", True))
    return await _show_quiz_menu(update, context)


async def qa_toggle_retake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    quiz_id = context.user_data.get("qa_quiz_id")
    quiz    = qs.get_quiz(quiz_id)
    qs.update_quiz_field(quiz_id, allow_retake=not quiz.get("allow_retake"))
    return await _show_quiz_menu(update, context)


# ── Delete quiz ───────────────────────────────────────────────────────────────

async def _show_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz_id = context.user_data.get("qa_quiz_id")
    quiz    = qs.get_quiz(quiz_id)
    if not quiz:
        await _reply(update, "⚠️ لم يعد هذا الاختبار موجوداً.", _hub_kb())
        return QA_HUB
    await _reply(
        update,
        f"🗑 هل أنت متأكد من حذف الاختبار:\n*{_md(quiz.get('name'))}*؟\n\n"
        "سيتم حذف جميع أسئلته ونتائجه نهائياً.",
        _yn("qa_delete_yes", "qa_back_to_quiz_menu"),
    )
    return QA_DEL_CONFIRM


async def qa_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_delete_confirm(update, context)


async def qa_delete_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    quiz_id = context.user_data.get("qa_quiz_id")
    qs.delete_quiz(quiz_id)
    context.user_data.pop("qa_quiz_id", None)
    await _reply(update, "✅ تم حذف الاختبار بنجاح.", _hub_kb())
    return QA_HUB


# ── Results ───────────────────────────────────────────────────────────────────

def _results_text(quiz_id) -> str:
    quiz    = qs.get_quiz(quiz_id)
    results = qs.results_for_quiz(quiz_id)
    lines = [f"📊 *نتائج اختبار: {_md(quiz.get('name', '—'))}*\n"]
    if not results:
        lines.append("لا توجد نتائج بعد.")
        return "\n".join(lines)

    for i, r in enumerate(results, 1):
        try:
            dt = datetime.fromisoformat(r.get("finished_at", "")).strftime("%Y-%m-%d %H:%M")
        except Exception:
            dt = "—"
        dur = int(r.get("duration_seconds", 0))
        m, s = divmod(dur, 60)
        lines.append(
            f"{i}. *{_md(r.get('user_name', '—'))}*\n"
            f"   الدرجة: {r.get('score', 0)}/{r.get('total_score', 0)}  ({r.get('percentage', 0)}٪)\n"
            f"   التاريخ: {dt}  |  المدة: {m} د {s} ث"
        )
    return "\n".join(lines)


async def _show_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz_id = context.user_data.get("qa_quiz_id")
    if not qs.get_quiz(quiz_id):
        await _reply(update, "⚠️ لم يعد هذا الاختبار موجوداً.", _hub_kb())
        return QA_HUB
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="qa_back_to_quiz_menu")]])
    await _reply(update, _results_text(quiz_id), kb)
    return QA_QUIZ_MENU


async def qa_show_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_results(update, context)


# ── Create new quiz ───────────────────────────────────────────────────────────

async def qa_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["qa_new"] = {}
    await _reply(update, "📝 أرسل *اسم الاختبار*:\n\nمثال: اختبار السيرة النبوية")
    return QA_C_NAME


async def qa_c_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if len(text) < 2:
        await update.message.reply_text("⚠️ الرجاء إدخال اسم صحيح.")
        return QA_C_NAME
    context.user_data["qa_new"]["name"] = text
    await update.message.reply_text("🗒 أرسل *وصف الاختبار* (اختياري — أرسل `-` للتخطي):",
                                     parse_mode=ParseMode.MARKDOWN)
    return QA_C_DESC


async def qa_c_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["qa_new"]["description"] = "" if text == "-" else text
    await update.message.reply_text("🔢 أرسل *درجة كل سؤال* (رقم):\n\nمثال: 5")
    return QA_C_POINTS


async def qa_c_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم صحيح أكبر من صفر.")
        return QA_C_POINTS
    context.user_data["qa_new"]["points_per_question"] = int(text)
    await update.message.reply_text("⏱ هل الاختبار محدد بوقت؟", reply_markup=_yn("qa_timed_yes", "qa_timed_no"))
    return QA_C_TIMED


async def qa_c_timed_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["qa_new"]["timed"] = False
    context.user_data["qa_new"]["time_minutes"] = None
    await _reply(update, "👁 هل يظهر الاختبار للمتسابقين الآن؟", _yn("qa_vis_yes", "qa_vis_no"))
    return QA_C_VISIBLE


async def qa_c_timed_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["qa_new"]["timed"] = True
    await _reply(update, "⏱ كم دقيقة؟\n\nمثال: 30")
    return QA_C_MINUTES


async def qa_c_minutes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم دقائق صحيح.")
        return QA_C_MINUTES
    context.user_data["qa_new"]["time_minutes"] = int(text)
    await update.message.reply_text("👁 هل يظهر الاختبار للمتسابقين الآن؟", reply_markup=_yn("qa_vis_yes", "qa_vis_no"))
    return QA_C_VISIBLE


async def _finish_create_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE, visible: bool):
    data = context.user_data.get("qa_new", {})
    quiz_id = qs.create_quiz(
        name=data.get("name", "بدون اسم"),
        description=data.get("description", ""),
        points_per_question=data.get("points_per_question", 1),
        timed=data.get("timed", False),
        time_minutes=data.get("time_minutes"),
        visible=visible,
    )
    context.user_data["qa_quiz_id"] = quiz_id
    context.user_data["qa_mode"] = "create"
    context.user_data.pop("qa_new", None)
    await _reply(
        update,
        "✅ تم إنشاء الاختبار. الآن أضف الأسئلة.\n\n"
        "📝 أرسل *نص السؤال الأول*:",
    )
    return QA_C_Q_TEXT


async def qa_c_visible_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _finish_create_quiz(update, context, True)


async def qa_c_visible_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _finish_create_quiz(update, context, False)


# ── Add-question loop (shared between "create new quiz" and "edit → add question") ──

async def qa_q_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("⚠️ الرجاء إدخال نص السؤال.")
        return QA_C_Q_TEXT
    context.user_data["qa_new_q"] = {"question": text}
    await update.message.reply_text("أ) أرسل *نص الخيار الأول*:")
    return QA_C_Q_OPT_A


async def qa_q_opt_a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["qa_new_q"]["option_a"] = text
    await update.message.reply_text("ب) أرسل *نص الخيار الثاني*:")
    return QA_C_Q_OPT_B


async def qa_q_opt_b(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["qa_new_q"]["option_b"] = text
    q = context.user_data["qa_new_q"]
    await update.message.reply_text(
        f"اختر *الإجابة الصحيحة*:\n\nأ) {q['option_a']}\nب) {q['option_b']}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ أ", callback_data="qa_correct_a"),
            InlineKeyboardButton("✅ ب", callback_data="qa_correct_b"),
        ]]),
    )
    return QA_C_Q_CORRECT


async def _save_new_question(update: Update, context: ContextTypes.DEFAULT_TYPE, correct: str):
    quiz_id = context.user_data.get("qa_quiz_id")
    q = context.user_data.get("qa_new_q", {})
    qs.add_question(quiz_id, q.get("question", ""), q.get("option_a", ""), q.get("option_b", ""), correct)
    context.user_data.pop("qa_new_q", None)
    await _reply(
        update,
        "✅ تمت إضافة السؤال.\n\nهل تريد إضافة سؤال آخر؟",
        InlineKeyboardMarkup([[
            InlineKeyboardButton("➕ سؤال آخر", callback_data="qa_q_more_yes"),
            InlineKeyboardButton("✅ إنهاء الاختبار", callback_data="qa_q_more_done"),
        ]]),
    )
    return QA_C_Q_MORE


async def qa_correct_a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _save_new_question(update, context, "a")


async def qa_correct_b(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _save_new_question(update, context, "b")


async def qa_q_more_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "📝 أرسل *نص السؤال* التالي:")
    return QA_C_Q_TEXT


async def qa_q_more_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    if context.user_data.get("qa_mode") == "edit_add":
        return await _show_question_list(update, context)
    return await _show_quiz_menu(update, context)


# ── Edit existing quiz ────────────────────────────────────────────────────────

def _edit_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 اسم الاختبار", callback_data="qa_e_name")],
        [InlineKeyboardButton("🗒 الوصف", callback_data="qa_e_desc")],
        [InlineKeyboardButton("🔢 درجة السؤال", callback_data="qa_e_points")],
        [InlineKeyboardButton("⏱ الوقت", callback_data="qa_e_timed")],
        [InlineKeyboardButton("❓ الأسئلة", callback_data="qa_e_questions")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="qa_back_to_quiz_menu")],
    ])


async def qa_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "✏️ *تعديل الاختبار*\n\nاختر ما تريد تعديله:", _edit_menu_kb())
    return QA_EDIT_MENU


async def qa_e_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "📝 أرسل الاسم الجديد للاختبار:")
    return QA_E_NAME


async def qa_e_name_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if len(text) < 2:
        await update.message.reply_text("⚠️ الرجاء إدخال اسم صحيح.")
        return QA_E_NAME
    qs.update_quiz_field(context.user_data.get("qa_quiz_id"), name=text)
    await update.message.reply_text("✅ تم تحديث اسم الاختبار.")
    return await qa_edit_menu_direct(update, context)


async def qa_e_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "🗒 أرسل الوصف الجديد (أرسل `-` لإفراغه):")
    return QA_E_DESC


async def qa_e_desc_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    qs.update_quiz_field(context.user_data.get("qa_quiz_id"), description="" if text == "-" else text)
    await update.message.reply_text("✅ تم تحديث الوصف.")
    return await qa_edit_menu_direct(update, context)


async def qa_e_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "🔢 أرسل درجة كل سؤال الجديدة (رقم):")
    return QA_E_POINTS


async def qa_e_points_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم صحيح أكبر من صفر.")
        return QA_E_POINTS
    qs.update_quiz_field(context.user_data.get("qa_quiz_id"), points_per_question=int(text))
    await update.message.reply_text("✅ تم تحديث درجة السؤال.")
    return await qa_edit_menu_direct(update, context)


async def qa_e_timed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "⏱ هل الاختبار محدد بوقت؟", _yn("qa_e_timed_yes", "qa_e_timed_no"))
    return QA_E_TIMED


async def qa_e_timed_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    qs.update_quiz_field(context.user_data.get("qa_quiz_id"), timed=False, time_minutes=None)
    await _reply(update, "✅ تم إلغاء تحديد الوقت.")
    return await qa_edit_menu_direct(update, context)


async def qa_e_timed_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "⏱ كم دقيقة؟")
    return QA_E_MINUTES


async def qa_e_minutes_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم دقائق صحيح.")
        return QA_E_MINUTES
    qs.update_quiz_field(context.user_data.get("qa_quiz_id"), timed=True, time_minutes=int(text))
    await update.message.reply_text("✅ تم تحديث وقت الاختبار.")
    return await qa_edit_menu_direct(update, context)


async def qa_edit_menu_direct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-show the edit menu after a text-based field update (no callback_query)."""
    await update.message.reply_text("✏️ *تعديل الاختبار*\n\nاختر ما تريد تعديله:",
                                     reply_markup=_edit_menu_kb(), parse_mode=ParseMode.MARKDOWN)
    return QA_EDIT_MENU


# ── Questions management inside edit ──────────────────────────────────────────

async def _show_question_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz_id = context.user_data.get("qa_quiz_id")
    quiz    = qs.get_quiz(quiz_id)
    questions = quiz.get("questions", [])
    rows = []
    for i, q in enumerate(questions):
        label = q.get("question", "")[:40] or f"سؤال {i + 1}"
        rows.append([InlineKeyboardButton(f"{i + 1}. {label}", callback_data=f"qa_qsel_{i}")])
    rows.append([InlineKeyboardButton("➕ إضافة سؤال", callback_data="qa_q_add_edit")])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="qa_edit_menu")])
    text = f"❓ *أسئلة الاختبار*  —  {len(questions)} سؤال\n\nاختر سؤالاً لتعديله:"
    await _reply(update, text, InlineKeyboardMarkup(rows))
    return QA_E_Q_LIST


async def qa_e_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_question_list(update, context)


async def qa_q_add_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["qa_mode"] = "edit_add"
    await _reply(update, "📝 أرسل *نص السؤال* الجديد:")
    return QA_C_Q_TEXT


def _q_field_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 نص السؤال", callback_data="qa_qf_text")],
        [InlineKeyboardButton("أ) الخيار الأول", callback_data="qa_qf_opta"),
         InlineKeyboardButton("ب) الخيار الثاني", callback_data="qa_qf_optb")],
        [InlineKeyboardButton("✅ الإجابة الصحيحة", callback_data="qa_qf_correct")],
        [InlineKeyboardButton("🗑 حذف السؤال", callback_data="qa_qf_delete")],
        [InlineKeyboardButton("🔙 رجوع لقائمة الأسئلة", callback_data="qa_back_to_q_list")],
    ])


def _q_summary(quiz: dict, idx: int) -> str:
    q = quiz.get("questions", [])[idx]
    correct = "أ" if q.get("correct") == "a" else "ب"
    return (
        f"*السؤال {idx + 1}*\n\n"
        f"{q.get('question', '')}\n\n"
        f"أ) {q.get('option_a', '')}\n"
        f"ب) {q.get('option_b', '')}\n\n"
        f"✅ الإجابة الصحيحة: *{correct}*"
    )


async def qa_qsel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    idx = int(update.callback_query.data.replace("qa_qsel_", ""))
    context.user_data["qa_q_index"] = idx
    quiz_id = context.user_data.get("qa_quiz_id")
    quiz    = qs.get_quiz(quiz_id)
    if idx >= len(quiz.get("questions", [])):
        return await _show_question_list(update, context)
    await _reply(update, _q_summary(quiz, idx) + "\n\nاختر ما تريد تعديله:", _q_field_kb())
    return QA_E_Q_FIELD_MENU


async def qa_back_to_q_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_question_list(update, context)


async def _show_q_field_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz_id = context.user_data.get("qa_quiz_id")
    idx     = context.user_data.get("qa_q_index", 0)
    quiz    = qs.get_quiz(quiz_id)
    if idx >= len(quiz.get("questions", [])):
        return await _show_question_list(update, context)
    await _reply(update, _q_summary(quiz, idx) + "\n\nاختر ما تريد تعديله:", _q_field_kb())
    return QA_E_Q_FIELD_MENU


async def qa_qf_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["qa_qf_pending"] = "question"
    await _reply(update, "📝 أرسل *نص السؤال* الجديد:")
    return QA_E_Q_FIELD_VAL


async def qa_qf_opta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["qa_qf_pending"] = "option_a"
    await _reply(update, "أ) أرسل *نص الخيار الأول* الجديد:")
    return QA_E_Q_FIELD_VAL


async def qa_qf_optb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["qa_qf_pending"] = "option_b"
    await _reply(update, "ب) أرسل *نص الخيار الثاني* الجديد:")
    return QA_E_Q_FIELD_VAL


async def qa_qf_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field   = context.user_data.get("qa_qf_pending")
    quiz_id = context.user_data.get("qa_quiz_id")
    idx     = context.user_data.get("qa_q_index", 0)
    text    = update.message.text.strip()
    if field and text:
        qs.update_question(quiz_id, idx, **{field: text})
    await update.message.reply_text("✅ تم التحديث.")
    quiz = qs.get_quiz(quiz_id)
    if idx >= len(quiz.get("questions", [])):
        await update.message.reply_text("❓ *أسئلة الاختبار*", parse_mode=ParseMode.MARKDOWN,
                                         reply_markup=InlineKeyboardMarkup(
                                             [[InlineKeyboardButton("🔙 رجوع", callback_data="qa_edit_menu")]]))
        return QA_E_Q_LIST
    await update.message.reply_text(
        _q_summary(quiz, idx) + "\n\nاختر ما تريد تعديله:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=_q_field_kb(),
    )
    return QA_E_Q_FIELD_MENU


async def qa_qf_correct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "اختر *الإجابة الصحيحة*:", InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ أ", callback_data="qa_qf_setcorrect_a"),
        InlineKeyboardButton("✅ ب", callback_data="qa_qf_setcorrect_b"),
    ]]))
    return QA_E_Q_FIELD_MENU


async def qa_qf_setcorrect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    correct = update.callback_query.data.replace("qa_qf_setcorrect_", "")
    quiz_id = context.user_data.get("qa_quiz_id")
    idx     = context.user_data.get("qa_q_index", 0)
    qs.update_question(quiz_id, idx, correct=correct)
    return await _show_q_field_menu(update, context)


async def qa_qf_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "🗑 هل تريد حذف هذا السؤال؟",
                 _yn("qa_qf_delete_yes", "qa_back_to_q_field"))
    return QA_E_Q_DEL_CONFIRM


async def qa_back_to_q_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_q_field_menu(update, context)


async def qa_qf_delete_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    quiz_id = context.user_data.get("qa_quiz_id")
    idx     = context.user_data.get("qa_q_index", 0)
    qs.delete_question(quiz_id, idx)
    await _reply(update, "✅ تم حذف السؤال.")
    return await _show_question_list(update, context)


# ── Cancel fallback ───────────────────────────────────────────────────────────

async def qa_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END


# ── Build the ConversationHandler ─────────────────────────────────────────────

def build_quiz_admin_handler() -> ConversationHandler:
    hub_reentry = CallbackQueryHandler(qa_hub, pattern="^qa_hub$")
    list_entry  = CallbackQueryHandler(qa_list_entry, pattern=r"^qa_list_(menu|edit|delete|toggle|results)$")

    return ConversationHandler(
        entry_points=[CallbackQueryHandler(qa_hub, pattern="^adm_quizzes$")],
        states={
            QA_HUB: [
                CallbackQueryHandler(qa_create, pattern="^qa_create$"),
                list_entry,
            ],
            QA_LIST: [
                CallbackQueryHandler(qa_pick, pattern=r"^qa_pick_\w+$"),
                list_entry, hub_reentry,
            ],
            QA_QUIZ_MENU: [
                CallbackQueryHandler(qa_edit_menu,        pattern="^qa_edit_menu$"),
                CallbackQueryHandler(qa_toggle_visible,   pattern="^qa_toggle_visible$"),
                CallbackQueryHandler(qa_toggle_score,     pattern="^qa_toggle_score$"),
                CallbackQueryHandler(qa_toggle_retake,    pattern="^qa_toggle_retake$"),
                CallbackQueryHandler(qa_show_results,     pattern="^qa_show_results$"),
                CallbackQueryHandler(qa_delete_confirm,   pattern="^qa_delete_confirm$"),
                CallbackQueryHandler(qa_back_to_quiz_menu, pattern="^qa_back_to_quiz_menu$"),
                list_entry, hub_reentry,
            ],
            QA_DEL_CONFIRM: [
                CallbackQueryHandler(qa_delete_yes,        pattern="^qa_delete_yes$"),
                CallbackQueryHandler(qa_back_to_quiz_menu,  pattern="^qa_back_to_quiz_menu$"),
                hub_reentry,
            ],
            # ── Create quiz ──
            QA_C_NAME:   [MessageHandler(filters.TEXT & ~filters.COMMAND, qa_c_name), hub_reentry],
            QA_C_DESC:   [MessageHandler(filters.TEXT & ~filters.COMMAND, qa_c_desc), hub_reentry],
            QA_C_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, qa_c_points), hub_reentry],
            QA_C_TIMED: [
                CallbackQueryHandler(qa_c_timed_yes, pattern="^qa_timed_yes$"),
                CallbackQueryHandler(qa_c_timed_no,  pattern="^qa_timed_no$"),
                hub_reentry,
            ],
            QA_C_MINUTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, qa_c_minutes), hub_reentry],
            QA_C_VISIBLE: [
                CallbackQueryHandler(qa_c_visible_yes, pattern="^qa_vis_yes$"),
                CallbackQueryHandler(qa_c_visible_no,  pattern="^qa_vis_no$"),
                hub_reentry,
            ],
            # ── Add-question loop (shared) ──
            QA_C_Q_TEXT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, qa_q_text), hub_reentry],
            QA_C_Q_OPT_A:  [MessageHandler(filters.TEXT & ~filters.COMMAND, qa_q_opt_a), hub_reentry],
            QA_C_Q_OPT_B:  [MessageHandler(filters.TEXT & ~filters.COMMAND, qa_q_opt_b), hub_reentry],
            QA_C_Q_CORRECT: [
                CallbackQueryHandler(qa_correct_a, pattern="^qa_correct_a$"),
                CallbackQueryHandler(qa_correct_b, pattern="^qa_correct_b$"),
                hub_reentry,
            ],
            QA_C_Q_MORE: [
                CallbackQueryHandler(qa_q_more_yes,  pattern="^qa_q_more_yes$"),
                CallbackQueryHandler(qa_q_more_done, pattern="^qa_q_more_done$"),
                hub_reentry,
            ],
            # ── Edit quiz ──
            QA_EDIT_MENU: [
                CallbackQueryHandler(qa_e_name,       pattern="^qa_e_name$"),
                CallbackQueryHandler(qa_e_desc,       pattern="^qa_e_desc$"),
                CallbackQueryHandler(qa_e_points,     pattern="^qa_e_points$"),
                CallbackQueryHandler(qa_e_timed,      pattern="^qa_e_timed$"),
                CallbackQueryHandler(qa_e_questions,  pattern="^qa_e_questions$"),
                CallbackQueryHandler(qa_back_to_quiz_menu, pattern="^qa_back_to_quiz_menu$"),
                hub_reentry,
            ],
            QA_E_NAME:   [MessageHandler(filters.TEXT & ~filters.COMMAND, qa_e_name_val), hub_reentry],
            QA_E_DESC:   [MessageHandler(filters.TEXT & ~filters.COMMAND, qa_e_desc_val), hub_reentry],
            QA_E_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, qa_e_points_val), hub_reentry],
            QA_E_TIMED: [
                CallbackQueryHandler(qa_e_timed_yes, pattern="^qa_e_timed_yes$"),
                CallbackQueryHandler(qa_e_timed_no,  pattern="^qa_e_timed_no$"),
                hub_reentry,
            ],
            QA_E_MINUTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, qa_e_minutes_val), hub_reentry],
            QA_E_Q_LIST: [
                CallbackQueryHandler(qa_qsel,       pattern=r"^qa_qsel_\d+$"),
                CallbackQueryHandler(qa_q_add_edit, pattern="^qa_q_add_edit$"),
                CallbackQueryHandler(qa_edit_menu,  pattern="^qa_edit_menu$"),
                hub_reentry,
            ],
            QA_E_Q_FIELD_MENU: [
                CallbackQueryHandler(qa_qf_text,        pattern="^qa_qf_text$"),
                CallbackQueryHandler(qa_qf_opta,         pattern="^qa_qf_opta$"),
                CallbackQueryHandler(qa_qf_optb,         pattern="^qa_qf_optb$"),
                CallbackQueryHandler(qa_qf_correct,      pattern="^qa_qf_correct$"),
                CallbackQueryHandler(qa_qf_setcorrect,   pattern=r"^qa_qf_setcorrect_(a|b)$"),
                CallbackQueryHandler(qa_qf_delete,       pattern="^qa_qf_delete$"),
                CallbackQueryHandler(qa_back_to_q_list,  pattern="^qa_back_to_q_list$"),
                hub_reentry,
            ],
            QA_E_Q_FIELD_VAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, qa_qf_val), hub_reentry],
            QA_E_Q_DEL_CONFIRM: [
                CallbackQueryHandler(qa_qf_delete_yes,   pattern="^qa_qf_delete_yes$"),
                CallbackQueryHandler(qa_back_to_q_field, pattern="^qa_back_to_q_field$"),
                hub_reentry,
            ],
        },
        fallbacks=[CommandHandler("cancel", qa_cancel)],
        allow_reentry=True,
        per_message=False,
    )
