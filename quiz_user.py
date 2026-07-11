# -*- coding: utf-8 -*-
"""
Participant-facing side of the '📝 إدارة الاختبارات' (Quiz Management) system.

Fully independent of the word-competition flow in handlers.py:
- Uses its own storage (quiz_storage.py).
- Every interaction here is a button tap (no free-text state), so it never
  touches the existing handle_message() text-state router.
"""
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from storage import get_user
import quiz_storage as qs

logger = logging.getLogger(__name__)

BACK_TO_MAIN = "back_to_main"  # reuse the existing main-menu back callback


def _back_kb(callback: str, label: str = "🔙 رجوع") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=callback)]])


async def _edit(update: Update, text: str, keyboard=None):
    kw = {"text": text, "parse_mode": ParseMode.MARKDOWN}
    if keyboard:
        kw["reply_markup"] = keyboard
    try:
        await update.callback_query.message.edit_text(**kw)
    except Exception as e:
        logger.warning("quiz_user _edit failed: %s", e)


def _total_points(quiz: dict) -> int:
    return len(quiz.get("questions", [])) * int(quiz.get("points_per_question", 0))


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    parts = []
    if h:
        parts.append(f"{h} ساعة")
    if m:
        parts.append(f"{m} دقيقة")
    if s or not parts:
        parts.append(f"{s} ثانية")
    return " و ".join(parts)


# ── Menu: list of visible quizzes ─────────────────────────────────────────────

async def handle_menu_quizzes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    quizzes = qs.visible_quizzes()
    if not quizzes:
        await _edit(update, "📝 لا توجد اختبارات متاحة حالياً.", _back_kb(BACK_TO_MAIN))
        return

    rows = [
        [InlineKeyboardButton(q.get("name", "—"), callback_data=f"qz_view_{qid}")]
        for qid, q in sorted(quizzes.items(), key=lambda kv: int(kv[0]))
    ]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=BACK_TO_MAIN)])
    await _edit(update, "📝 *الاختبارات المتاحة*\n\nاختر اختباراً:", InlineKeyboardMarkup(rows))


# ── Quiz info screen ──────────────────────────────────────────────────────────

async def handle_quiz_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    user_id = query.from_user.id
    quiz_id = query.data.replace("qz_view_", "")
    quiz    = qs.get_quiz(quiz_id)

    if not quiz or not quiz.get("visible"):
        await query.answer("⚠️ هذا الاختبار غير متاح.", show_alert=True)
        return
    await query.answer()

    if not quiz.get("allow_retake") and qs.has_taken_quiz(quiz_id, user_id):
        await _edit(
            update,
            f"📝 *{quiz.get('name')}*\n\n"
            "✅ لقد قمت بحل هذا الاختبار مسبقاً، ولا يمكن إعادته.",
            _back_kb("menu_quizzes"),
        )
        return

    n_q     = len(quiz.get("questions", []))
    total   = _total_points(quiz)
    time_line = f"⏱ الوقت: *{quiz.get('time_minutes')}* دقيقة" if quiz.get("timed") else "⏱ الوقت: بدون تحديد"
    desc    = quiz.get("description") or ""
    lines = [
        f"📝 *{quiz.get('name')}*",
    ]
    if desc:
        lines.append(f"_{desc}_")
    lines += [
        "",
        f"❓ عدد الأسئلة: *{n_q}*",
        f"🏆 الدرجة الكلية: *{total}*",
        time_line,
    ]
    if n_q == 0:
        lines.append("\n⚠️ لا توجد أسئلة في هذا الاختبار بعد.")
        kb = _back_kb("menu_quizzes")
    else:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ بدء الاختبار", callback_data=f"qz_start_{quiz_id}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="menu_quizzes")],
        ])
    await _edit(update, "\n".join(lines), kb)


# ── Starting / running the quiz ───────────────────────────────────────────────

def _question_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("أ", callback_data="qz_ans_a"),
        InlineKeyboardButton("ب", callback_data="qz_ans_b"),
    ]])


def _question_text(quiz: dict, index: int) -> str:
    q = quiz["questions"][index]
    n = len(quiz["questions"])
    return (
        f"📝 *{quiz.get('name')}*  —  السؤال {index + 1}/{n}\n\n"
        f"{q.get('question', '')}\n\n"
        f"أ) {q.get('option_a', '')}\n"
        f"ب) {q.get('option_b', '')}"
    )


async def handle_quiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    user_id = query.from_user.id
    quiz_id = query.data.replace("qz_start_", "")
    quiz    = qs.get_quiz(quiz_id)

    if not quiz or not quiz.get("visible") or not quiz.get("questions"):
        await query.answer("⚠️ هذا الاختبار غير متاح.", show_alert=True)
        return
    if not quiz.get("allow_retake") and qs.has_taken_quiz(quiz_id, user_id):
        await query.answer("✅ لقد قمت بحل هذا الاختبار مسبقاً.", show_alert=True)
        return

    await query.answer()
    qs.start_session(user_id, quiz_id)
    await _edit(update, _question_text(quiz, 0), _question_kb())


async def _finish_quiz(update: Update, user_id: int, quiz: dict, session: dict) -> None:
    quiz_id       = session["quiz_id"]
    n_q           = len(quiz.get("questions", []))
    correct_count = session.get("correct_count", 0)
    wrong_count   = n_q - correct_count
    points_each   = int(quiz.get("points_per_question", 0))
    score         = correct_count * points_each
    total_points  = n_q * points_each
    percentage    = round((correct_count / n_q) * 100, 1) if n_q else 0.0

    started_at = session.get("started_at")
    try:
        duration = (datetime.utcnow() - datetime.fromisoformat(started_at)).total_seconds()
    except Exception:
        duration = 0

    user = get_user(user_id) or {}
    qs.add_quiz_result({
        "quiz_id":     str(quiz_id),
        "user_id":     str(user_id),
        "user_name":   user.get("full_name") or "—",
        "score":       score,
        "total_score": total_points,
        "correct":     correct_count,
        "wrong":       wrong_count,
        "percentage":  percentage,
        "started_at":  started_at,
        "finished_at": datetime.utcnow().isoformat(),
        "duration_seconds": int(duration),
    })
    qs.end_session(user_id)

    if quiz.get("show_score", True):
        text = (
            "✅ *انتهى الاختبار*\n\n"
            f"🏆 الدرجة: *{score}* من *{total_points}*\n"
            f"✔️ الإجابات الصحيحة: *{correct_count}*\n"
            f"❌ الإجابات الخاطئة: *{wrong_count}*\n"
            f"📊 النسبة المئوية: *{percentage}٪*"
        )
    else:
        text = "✅ *انتهى الاختبار*\n\nوفقكم الله 🌿"

    await _edit(update, text, _back_kb("menu_quizzes"))


async def handle_quiz_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    user_id = query.from_user.id
    choice  = query.data.replace("qz_ans_", "")  # "a" or "b"

    session = qs.get_session(user_id)
    if not session:
        await query.answer("⚠️ لا يوجد اختبار جارٍ. استخدم /start.", show_alert=True)
        return

    quiz_id = session["quiz_id"]
    quiz    = qs.get_quiz(quiz_id)
    if not quiz or not quiz.get("questions"):
        qs.end_session(user_id)
        await query.answer("⚠️ حدث خطأ في الاختبار.", show_alert=True)
        return

    await query.answer()

    # Time-limit check — if the allotted time has already elapsed, grade
    # whatever was answered so far and end the attempt now.
    if quiz.get("timed"):
        try:
            started = datetime.fromisoformat(session["started_at"])
            elapsed = (datetime.utcnow() - started).total_seconds()
        except Exception:
            elapsed = 0
        if elapsed > int(quiz.get("time_minutes", 0)) * 60:
            await _finish_quiz(update, user_id, quiz, session)
            return

    questions = quiz["questions"]
    index     = session.get("current_index", 0)
    if index >= len(questions):
        await _finish_quiz(update, user_id, quiz, session)
        return

    correct_choice = questions[index].get("correct")
    is_correct     = (choice == correct_choice)

    answers = list(session.get("answers", []))
    answers.append(choice)
    correct_count = session.get("correct_count", 0) + (1 if is_correct else 0)
    next_index    = index + 1

    session = qs.update_session(
        user_id, answers=answers, correct_count=correct_count, current_index=next_index,
    )

    if next_index >= len(questions):
        await _finish_quiz(update, user_id, quiz, session)
        return

    await _edit(update, _question_text(quiz, next_index), _question_kb())
