# -*- coding: utf-8 -*-
"""
Admin side of '👥 اختبار توزيع الفرق' (Team-Distribution Test) — a completely
independent quiz-like feature whose only purpose is to rank participants by
knowledge level and then split them into balanced teams.

Design (same proven pattern as quiz_admin.py / admin_settings.py):
- Its own, fully independent ConversationHandler, entered via the
  "adm_distro" callback button (the only line added to admin.py's keyboard —
  see admin.py's _main_kb()).
- Because python-telegram-bot's ConversationHandler ignores updates that
  don't match its current state instead of consuming them, pressing
  "⬅️ القائمة الرئيسية" (callback_data="adm_main") from anywhere in this
  conversation is NOT handled here — it falls through untouched to the
  original admin ConversationHandler (still parked in its MAIN state),
  which shows the normal admin main menu exactly as before.
- All data lives in distro_storage.py (its own JSON files). Nothing here
  reads or writes days.json, users.json, config.json, credit_log.json,
  transactions.json, recharge_codes.json, admin_log.json, quizzes.json,
  quiz_results.json, quiz_sessions.json, or quiz_credit_log.json.
- No points are ever added to any participant's balance from this feature —
  there is no call anywhere in this file to credits.add_credits() or
  transactions.record(). Scores exist ONLY to rank participants for the
  team split; this test never appears on the leaderboard.
"""
import io
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters,
)
from telegram.constants import ParseMode

import admins_store
import distro_storage as ds

logger = logging.getLogger(__name__)

# ── States ────────────────────────────────────────────────────────────────────
(DT_HUB, DT_LIST, DT_QUIZ_MENU, DT_DEL_CONFIRM,
 DT_C_NAME, DT_C_COUNT, DT_C_TIMED, DT_C_MINUTES, DT_C_VISIBLE,
 DT_C_Q_TEXT, DT_C_Q_OPT_A, DT_C_Q_OPT_B, DT_C_Q_CORRECT,
 DT_SPLIT_SIZE, DT_SPLIT_VIEW,
 ) = range(15)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_admin(uid: int) -> bool:
    return admins_store.is_admin(uid)


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
        [InlineKeyboardButton("➕ إنشاء اختبار توزيع", callback_data="dt_create")],
        [InlineKeyboardButton("📋 قائمة اختبارات التوزيع", callback_data="dt_list_menu")],
        [InlineKeyboardButton("👥 تقسيم الفرق", callback_data="dt_list_split")],
        [InlineKeyboardButton("📊 نتائج اختبار التوزيع", callback_data="dt_list_results")],
        [InlineKeyboardButton("🗑 حذف اختبار", callback_data="dt_list_delete")],
        [InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="adm_main")],
    ])


async def dt_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer("⛔ ليس لديك صلاحية.", show_alert=True)
        return ConversationHandler.END
    if update.callback_query:
        await update.callback_query.answer()
    context.user_data.pop("dt_quiz_id", None)
    context.user_data.pop("dt_action", None)
    context.user_data.pop("dt_new", None)
    await _reply(update, "👥 *اختبار توزيع الفرق*\n\nاختر العملية:", _hub_kb())
    return DT_HUB


# ── Quiz list (shared by menu/delete/results/split actions) ─────────────────

def _quiz_list_kb() -> InlineKeyboardMarkup:
    quizzes = ds.load_quizzes()
    rows = [
        [InlineKeyboardButton(q.get("name", "—"), callback_data=f"dt_pick_{qid}")]
        for qid, q in sorted(quizzes.items(), key=lambda kv: int(kv[0]))
    ]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="dt_hub")])
    return InlineKeyboardMarkup(rows)


async def dt_list_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    action = query.data.replace("dt_list_", "")  # menu | delete | results | split
    context.user_data["dt_action"] = action

    quizzes = ds.load_quizzes()
    if not quizzes:
        await _reply(
            update, "📭 لا توجد اختبارات توزيع بعد.\n\nأنشئ اختباراً جديداً أولاً.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="dt_hub")]]),
        )
        return DT_LIST

    await _reply(update, "📋 *اختبارات توزيع الفرق*\n\nاختر اختباراً:", _quiz_list_kb())
    return DT_LIST


async def dt_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    quiz_id = query.data.replace("dt_pick_", "")
    context.user_data["dt_quiz_id"] = quiz_id
    action = context.user_data.get("dt_action", "menu")

    if action == "delete":
        return await _show_delete_confirm(update, context)
    if action == "results":
        return await _show_results(update, context)
    if action == "split":
        return await _ask_team_size(update, context)
    return await _show_quiz_menu(update, context)


# ── Per-quiz menu ─────────────────────────────────────────────────────────────

def _quiz_menu_kb(quiz: dict) -> InlineKeyboardMarkup:
    vis = "👁 إخفاء الاختبار" if quiz.get("visible") else "👁 إظهار الاختبار"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(vis, callback_data="dt_toggle_visible")],
        [InlineKeyboardButton("📊 عرض النتائج", callback_data="dt_show_results")],
        [InlineKeyboardButton("👥 تقسيم الفرق", callback_data="dt_goto_split")],
        [InlineKeyboardButton("🗑 حذف", callback_data="dt_delete_confirm")],
        [InlineKeyboardButton("🔙 رجوع لقائمة اختبارات التوزيع", callback_data="dt_list_menu")],
        [InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="adm_main")],
    ])


def _quiz_summary(quiz: dict) -> str:
    n_q = len(quiz.get("questions", []))
    time_line = f"{quiz.get('time_minutes')} دقيقة" if quiz.get("timed") else "بدون تحديد"
    n_results = len(ds.results_for_quiz(quiz.get("id")))
    lines = [
        f"👥 *{_md(quiz.get('name', '—'))}*",
        "",
        f"❓ عدد الأسئلة: *{n_q}*",
        f"⏱ الوقت: *{time_line}*",
        f"👁 ظاهر للمتسابقين: *{'نعم' if quiz.get('visible') else 'لا'}*",
        f"✅ عدد من أنهى الاختبار: *{n_results}*",
    ]
    return "\n".join(lines)


async def _show_quiz_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz_id = context.user_data.get("dt_quiz_id")
    quiz    = ds.get_quiz(quiz_id)
    if not quiz:
        await _reply(update, "⚠️ لم يعد هذا الاختبار موجوداً.", _hub_kb())
        return DT_HUB
    await _reply(update, _quiz_summary(quiz) + "\n\nاختر العملية:", _quiz_menu_kb(quiz))
    return DT_QUIZ_MENU


async def dt_back_to_quiz_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_quiz_menu(update, context)


async def dt_toggle_visible(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    quiz_id = context.user_data.get("dt_quiz_id")
    quiz    = ds.get_quiz(quiz_id)
    ds.set_quiz_visible(quiz_id, not quiz.get("visible"))
    return await _show_quiz_menu(update, context)


async def dt_goto_split(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _ask_team_size(update, context)


# ── Delete quiz ───────────────────────────────────────────────────────────────

async def _show_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz_id = context.user_data.get("dt_quiz_id")
    quiz    = ds.get_quiz(quiz_id)
    if not quiz:
        await _reply(update, "⚠️ لم يعد هذا الاختبار موجوداً.", _hub_kb())
        return DT_HUB
    await _reply(
        update,
        f"🗑 هل أنت متأكد من حذف اختبار التوزيع:\n*{_md(quiz.get('name'))}*؟\n\n"
        "سيتم حذف جميع أسئلته ونتائجه وتقسيماته المحفوظة نهائياً.",
        _yn("dt_delete_yes", "dt_back_to_quiz_menu"),
    )
    return DT_DEL_CONFIRM


async def dt_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_delete_confirm(update, context)


async def dt_delete_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    quiz_id = context.user_data.get("dt_quiz_id")
    ds.delete_quiz(quiz_id)
    context.user_data.pop("dt_quiz_id", None)
    await _reply(update, "✅ تم حذف اختبار التوزيع بنجاح.", _hub_kb())
    return DT_HUB


# ── Results ───────────────────────────────────────────────────────────────────

def _results_text(quiz_id) -> str:
    quiz    = ds.get_quiz(quiz_id)
    results = sorted(ds.results_for_quiz(quiz_id), key=lambda r: -int(r.get("score", 0)))
    lines = [f"📊 *نتائج اختبار التوزيع: {_md(quiz.get('name', '—'))}*\n"]
    if not results:
        lines.append("لا توجد نتائج بعد.")
        return "\n".join(lines)
    for i, r in enumerate(results, 1):
        lines.append(
            f"{i}. *{_md(r.get('user_name', '—'))}*  —  "
            f"الدرجة: {r.get('score', 0)}/{r.get('total_score', 0)}"
        )
    return "\n".join(lines)


async def _show_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz_id = context.user_data.get("dt_quiz_id")
    if not ds.get_quiz(quiz_id):
        await _reply(update, "⚠️ لم يعد هذا الاختبار موجوداً.", _hub_kb())
        return DT_HUB
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="dt_back_to_quiz_menu")]])
    await _reply(update, _results_text(quiz_id), kb)
    return DT_QUIZ_MENU


async def dt_show_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_results(update, context)


# ── Team split ("👥 تقسيم الفرق") ─────────────────────────────────────────────

async def _ask_team_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz_id = context.user_data.get("dt_quiz_id")
    quiz    = ds.get_quiz(quiz_id)
    if not quiz:
        await _reply(update, "⚠️ لم يعد هذا الاختبار موجوداً.", _hub_kb())
        return DT_HUB
    if not ds.results_for_quiz(quiz_id):
        await _reply(
            update,
            f"📭 لا يوجد أي متسابق أنهى اختبار *{_md(quiz.get('name'))}* بعد.\n\n"
            "انتظر حتى ينتهي المتسابقون من الاختبار قبل التقسيم.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="dt_hub")]]),
        )
        return DT_HUB
    await _reply(update, "🔢 كم عدد أعضاء كل فريق؟\n\nمثال: 5")
    return DT_SPLIT_SIZE


def _split_text(quiz_name: str, teams: list) -> str:
    lines = [f"👥 *تقسيم فرق: {_md(quiz_name)}*\n"]
    for team in teams:
        lines.append(f"\n🔹 *{team['name']}*")
        for m in team["members"]:
            lines.append(f"   • {_md(m['user_name'])} — {m['score']} نقطة")
        lines.append(f"   ➖ المجموع: *{team['total']}*  |  المتوسط: *{team['average']}*")
    return "\n".join(lines)


def _split_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 تصدير الفرق", callback_data="dt_export_teams")],
        [InlineKeyboardButton("🔁 إعادة التقسيم", callback_data="dt_resplit")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="dt_hub")],
        [InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="adm_main")],
    ])


async def dt_split_size_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم صحيح أكبر من صفر.")
        return DT_SPLIT_SIZE

    quiz_id = context.user_data.get("dt_quiz_id")
    quiz    = ds.get_quiz(quiz_id)
    if not quiz:
        await update.message.reply_text("⚠️ لم يعد هذا الاختبار موجوداً.", reply_markup=_hub_kb())
        return DT_HUB

    teams = ds.compute_teams(quiz_id, int(text))
    if not teams:
        await update.message.reply_text(
            "📭 لا توجد نتائج كافية لتقسيم الفرق بعد.", reply_markup=_hub_kb(),
        )
        return DT_HUB

    ds.set_team_split(quiz_id, int(text), teams)
    await update.message.reply_text(
        _split_text(quiz.get("name", "—"), teams),
        reply_markup=_split_kb(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return DT_SPLIT_VIEW


async def dt_resplit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "🔢 كم عدد أعضاء كل فريق؟\n\nمثال: 5")
    return DT_SPLIT_SIZE


async def dt_export_teams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer("جاري إنشاء الملف…")
    quiz_id = context.user_data.get("dt_quiz_id")
    quiz    = ds.get_quiz(quiz_id)
    split   = ds.get_team_split(quiz_id)
    if not quiz or not split:
        await query.message.reply_text("⚠️ لا يوجد تقسيم محفوظ لتصديره. استخدم 🔁 إعادة التقسيم أولاً.")
        return DT_SPLIT_VIEW

    # Lazy import: keeps this feature's failure isolated to the export button
    # (rather than crashing the whole bot at startup) if openpyxl is missing.
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "تقسيم الفرق"
    ws.sheet_view.rightToLeft = True

    headers = ["اسم الفريق", "اسم المتسابق", "درجة اختبار التوزيع", "مجموع الفريق"]
    h_fill = PatternFill("solid", fgColor="1F4E79")
    h_font = Font(bold=True, color="FFFFFF", name="Arial", size=12)
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = h_fill
        cell.font = h_font
        cell.alignment = Alignment(horizontal="center")
    ws.row_dimensions[1].height = 22

    for team in split["teams"]:
        for m in team["members"]:
            ws.append([team["name"], m["user_name"], m["score"], team["total"]])

    for idx, w in enumerate([20, 28, 22, 16], 1):
        ws.column_dimensions[ws.cell(1, idx).column_letter].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    await query.message.reply_document(
        document=buf,
        filename=f"teams_{quiz.get('name','distro')}.xlsx",
        caption="📄 تم تصدير تقسيم الفرق.",
    )
    return DT_SPLIT_VIEW


# ── Create new distribution test ─────────────────────────────────────────────

async def dt_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["dt_new"] = {}
    await _reply(update, "📝 أرسل *اسم اختبار التوزيع*:\n\nمثال: اختبار توزيع فرق المخيم")
    return DT_C_NAME


async def dt_c_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if len(text) < 2:
        await update.message.reply_text("⚠️ الرجاء إدخال اسم صحيح.")
        return DT_C_NAME
    context.user_data["dt_new"]["name"] = text
    await update.message.reply_text("🔢 كم عدد الأسئلة؟ (رقم)")
    return DT_C_COUNT


async def dt_c_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم صحيح أكبر من صفر.")
        return DT_C_COUNT
    context.user_data["dt_new"]["question_count"] = int(text)
    await update.message.reply_text("⏱ هل الاختبار محدد بوقت؟", reply_markup=_yn("dt_timed_yes", "dt_timed_no"))
    return DT_C_TIMED


async def dt_c_timed_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["dt_new"]["timed"] = False
    context.user_data["dt_new"]["time_minutes"] = None
    await _reply(update, "👁 هل يظهر الاختبار للمتسابقين الآن؟", _yn("dt_vis_yes", "dt_vis_no"))
    return DT_C_VISIBLE


async def dt_c_timed_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["dt_new"]["timed"] = True
    await _reply(update, "⏱ كم دقيقة؟\n\nمثال: 30")
    return DT_C_MINUTES


async def dt_c_minutes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم دقائق صحيح.")
        return DT_C_MINUTES
    context.user_data["dt_new"]["time_minutes"] = int(text)
    await update.message.reply_text("👁 هل يظهر الاختبار للمتسابقين الآن؟", reply_markup=_yn("dt_vis_yes", "dt_vis_no"))
    return DT_C_VISIBLE


async def _finish_create_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE, visible: bool):
    new = context.user_data["dt_new"]
    quiz_id = ds.create_quiz(new["name"], new["timed"], new.get("time_minutes"), visible)
    new["quiz_id"] = quiz_id
    new["q_index"] = 0
    await _reply(update, f"❓ أرسل نص السؤال 1 من {new['question_count']}:")
    return DT_C_Q_TEXT


async def dt_c_visible_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _finish_create_quiz(update, context, True)


async def dt_c_visible_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _finish_create_quiz(update, context, False)


async def dt_q_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if len(text) < 2:
        await update.message.reply_text("⚠️ الرجاء إدخال نص سؤال صحيح.")
        return DT_C_Q_TEXT
    context.user_data["dt_new"]["cur_q"] = text
    await update.message.reply_text("🅰️ أرسل نص الخيار (أ):")
    return DT_C_Q_OPT_A


async def dt_q_opt_a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("⚠️ الرجاء إدخال نص صحيح.")
        return DT_C_Q_OPT_A
    context.user_data["dt_new"]["cur_a"] = text
    await update.message.reply_text("🅱️ أرسل نص الخيار (ب):")
    return DT_C_Q_OPT_B


async def dt_q_opt_b(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("⚠️ الرجاء إدخال نص صحيح.")
        return DT_C_Q_OPT_B
    context.user_data["dt_new"]["cur_b"] = text
    await update.message.reply_text(
        "✅ ما الإجابة الصحيحة؟",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("أ", callback_data="dt_correct_a"),
            InlineKeyboardButton("ب", callback_data="dt_correct_b"),
        ]]),
    )
    return DT_C_Q_CORRECT


async def _save_new_question(update: Update, context: ContextTypes.DEFAULT_TYPE, correct: str):
    new = context.user_data["dt_new"]
    ds.add_question(new["quiz_id"], new["cur_q"], new["cur_a"], new["cur_b"], correct)
    new["q_index"] += 1

    if new["q_index"] >= new["question_count"]:
        quiz = ds.get_quiz(new["quiz_id"])
        await _reply(
            update,
            "✅ *تم إنشاء اختبار التوزيع بنجاح.*\n\n" + _quiz_summary(quiz),
            _hub_kb(),
        )
        context.user_data.pop("dt_new", None)
        return DT_HUB

    await _reply(update, f"❓ أرسل نص السؤال {new['q_index'] + 1} من {new['question_count']}:")
    return DT_C_Q_TEXT


async def dt_correct_a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _save_new_question(update, context, "a")


async def dt_correct_b(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _save_new_question(update, context, "b")


# ── Cancel fallback ───────────────────────────────────────────────────────────

async def dt_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END


# ── Build the ConversationHandler ─────────────────────────────────────────────

def build_distro_admin_handler() -> ConversationHandler:
    hub_reentry = CallbackQueryHandler(dt_hub, pattern="^dt_hub$")
    list_entry  = CallbackQueryHandler(dt_list_entry, pattern=r"^dt_list_(menu|delete|results|split)$")

    return ConversationHandler(
        entry_points=[CallbackQueryHandler(dt_hub, pattern="^adm_distro$")],
        states={
            DT_HUB: [
                CallbackQueryHandler(dt_create, pattern="^dt_create$"),
                list_entry,
            ],
            DT_LIST: [
                CallbackQueryHandler(dt_pick, pattern=r"^dt_pick_\w+$"),
                list_entry, hub_reentry,
            ],
            DT_QUIZ_MENU: [
                CallbackQueryHandler(dt_toggle_visible,    pattern="^dt_toggle_visible$"),
                CallbackQueryHandler(dt_show_results,      pattern="^dt_show_results$"),
                CallbackQueryHandler(dt_goto_split,        pattern="^dt_goto_split$"),
                CallbackQueryHandler(dt_delete_confirm,    pattern="^dt_delete_confirm$"),
                CallbackQueryHandler(dt_back_to_quiz_menu, pattern="^dt_back_to_quiz_menu$"),
                list_entry, hub_reentry,
            ],
            DT_DEL_CONFIRM: [
                CallbackQueryHandler(dt_delete_yes,        pattern="^dt_delete_yes$"),
                CallbackQueryHandler(dt_back_to_quiz_menu, pattern="^dt_back_to_quiz_menu$"),
                hub_reentry,
            ],
            # ── Create ──
            DT_C_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, dt_c_name), hub_reentry],
            DT_C_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, dt_c_count), hub_reentry],
            DT_C_TIMED: [
                CallbackQueryHandler(dt_c_timed_yes, pattern="^dt_timed_yes$"),
                CallbackQueryHandler(dt_c_timed_no,  pattern="^dt_timed_no$"),
                hub_reentry,
            ],
            DT_C_MINUTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, dt_c_minutes), hub_reentry],
            DT_C_VISIBLE: [
                CallbackQueryHandler(dt_c_visible_yes, pattern="^dt_vis_yes$"),
                CallbackQueryHandler(dt_c_visible_no,  pattern="^dt_vis_no$"),
                hub_reentry,
            ],
            DT_C_Q_TEXT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, dt_q_text), hub_reentry],
            DT_C_Q_OPT_A:  [MessageHandler(filters.TEXT & ~filters.COMMAND, dt_q_opt_a), hub_reentry],
            DT_C_Q_OPT_B:  [MessageHandler(filters.TEXT & ~filters.COMMAND, dt_q_opt_b), hub_reentry],
            DT_C_Q_CORRECT: [
                CallbackQueryHandler(dt_correct_a, pattern="^dt_correct_a$"),
                CallbackQueryHandler(dt_correct_b, pattern="^dt_correct_b$"),
                hub_reentry,
            ],
            # ── Team split ──
            DT_SPLIT_SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, dt_split_size_val), hub_reentry],
            DT_SPLIT_VIEW: [
                CallbackQueryHandler(dt_export_teams, pattern="^dt_export_teams$"),
                CallbackQueryHandler(dt_resplit,      pattern="^dt_resplit$"),
                hub_reentry,
            ],
        },
        fallbacks=[CommandHandler("cancel", dt_cancel)],
        allow_reentry=True,
        per_message=False,
    )
