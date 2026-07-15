# -*- coding: utf-8 -*-
"""
Participant-facing side of '👥 اختبار توزيع الفرق' (Team-Distribution Test).

Fully independent of the word-competition flow (handlers.py) and of the
existing quiz system (quiz_user.py):
- Uses its own storage (distro_storage.py).
- Every interaction here is a button tap (no free-text state), so it never
  touches handle_message()'s text-state router.
- Finishing this test NEVER adds points to the participant's balance and
  NEVER affects the leaderboard — its only purpose is to produce a score
  the admin can later use to build balanced teams (see distro_admin.py).
- Retakes are never allowed, so every participant's ranking reflects one
  honest attempt.

Timed-test behavior mirrors quiz_user.py exactly: the clock starts the
moment the admin makes the test visible, a live countdown is kept up to
date via a repeating JobQueue job, and running out of time closes the
attempt with NOTHING recorded (no score at all for that participant).
"""
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from storage import get_user
import distro_storage as ds

logger = logging.getLogger(__name__)

BACK_TO_MAIN = "back_to_main"  # reuse the existing main-menu back callback
TIMER_TICK_SECONDS = 5

EXPIRED_MSG = (
    "⏰ انتهى وقت الاختبار قبل تسليم الإجابة.\n\n"
    "لم يتم احتساب درجتك."
)


def _back_kb(callback: str, label: str = "🔙 رجوع") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=callback)]])


async def _edit(update: Update, text: str, keyboard=None):
    kw = {"text": text, "parse_mode": ParseMode.MARKDOWN}
    if keyboard:
        kw["reply_markup"] = keyboard
    try:
        await update.callback_query.message.edit_text(**kw)
    except Exception as e:
        logger.warning("distro_user _edit failed: %s", e)


def _fmt_timer(seconds) -> str:
    seconds = max(0, int(seconds or 0))
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


def _timer_job_name(user_id: int) -> str:
    return f"dz_timer_{user_id}"


def _cancel_timer(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    jq = context.job_queue
    if not jq:
        return
    for job in jq.get_jobs_by_name(_timer_job_name(user_id)):
        job.schedule_removal()


def _schedule_timer(context: ContextTypes.DEFAULT_TYPE, user_id: int,
                     chat_id: int, message_id: int) -> None:
    _cancel_timer(context, user_id)
    jq = context.job_queue
    if not jq:
        logger.warning("job_queue unavailable - live distro-test countdown disabled.")
        return
    jq.run_repeating(
        _timer_tick,
        interval=TIMER_TICK_SECONDS,
        first=TIMER_TICK_SECONDS,
        data={"user_id": user_id, "chat_id": chat_id, "message_id": message_id},
        name=_timer_job_name(user_id),
    )


async def _timer_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    job     = context.job
    data    = job.data
    user_id = data["user_id"]

    session = ds.get_session(user_id)
    if not session:
        job.schedule_removal()
        return

    quiz = ds.get_quiz(session.get("quiz_id"))
    if not quiz or not quiz.get("timed"):
        job.schedule_removal()
        return

    remaining = ds.remaining_seconds(quiz)
    if remaining is None:
        job.schedule_removal()
        return

    if remaining <= 0:
        job.schedule_removal()
        ds.end_session(user_id)  # expired - no score, nothing recorded
        try:
            await context.bot.edit_message_text(
                chat_id=data["chat_id"], message_id=data["message_id"],
                text=EXPIRED_MSG, parse_mode=ParseMode.MARKDOWN,
                reply_markup=_back_kb("menu_distro"),
            )
        except Exception as e:
            logger.warning("distro timer expiry edit failed: %s", e)
        return

    index = session.get("current_index", 0)
    if index >= len(quiz.get("questions", [])):
        job.schedule_removal()
        return

    sq = ds.get_shuffled_question(quiz, session, index)
    if not sq:
        job.schedule_removal()
        return

    try:
        await context.bot.edit_message_text(
            chat_id=data["chat_id"], message_id=data["message_id"],
            text=_question_text(quiz, sq, index, remaining),
            parse_mode=ParseMode.MARKDOWN, reply_markup=_question_kb(),
        )
    except Exception as e:
        logger.debug("distro timer tick edit skipped: %s", e)


async def handle_menu_distro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    quizzes = ds.visible_quizzes()
    if not quizzes:
        await _edit(update, "👥 لا يوجد اختبار توزيع متاح حالياً.", _back_kb(BACK_TO_MAIN))
        return

    rows = [
        [InlineKeyboardButton(q.get("name", "—"), callback_data=f"dz_view_{qid}")]
        for qid, q in sorted(quizzes.items(), key=lambda kv: int(kv[0]))
    ]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=BACK_TO_MAIN)])
    await _edit(update, "👥 *اختبار تنظيمي*\n\nاختر اختباراً:", InlineKeyboardMarkup(rows))


async def handle_distro_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    user_id = query.from_user.id
    quiz_id = query.data.replace("dz_view_", "")
    quiz    = ds.get_quiz(quiz_id)

    if not quiz or not quiz.get("visible"):
        await query.answer("⚠️ هذا الاختبار غير متاح.", show_alert=True)
        return
    await query.answer()

    if ds.has_taken_quiz(quiz_id, user_id):
        await _edit(
            update,
            f"👥 *{quiz.get('name')}*\n\n"
            "✅ لقد قمت بحل هذا الاختبار مسبقاً، ولا يمكن إعادته.",
            _back_kb("menu_distro"),
        )
        return

    remaining = ds.remaining_seconds(quiz)  # None if untimed
    if remaining is not None and remaining <= 0:
        await _edit(
            update,
            f"👥 *{quiz.get('name')}*\n\n"
            "⏰ انتهى وقت هذا الاختبار، ولم يعد بالإمكان البدء به.",
            _back_kb("menu_distro"),
        )
        return

    if not ds.is_entry_open(quiz):
        await _edit(
            update,
            f"👥 *{quiz.get('name')}*\n\n"
            "🚪 الدخول لهذا الاختبار مغلق حالياً من قبل المشرف.",
            _back_kb("menu_distro"),
        )
        return

    n_q = len(quiz.get("questions", []))
    if remaining is not None:
        time_line = f"⏳ الوقت المتبقي حتى إغلاق الاختبار: *{_fmt_timer(remaining)}*"
    else:
        time_line = "⏱ الوقت: بدون تحديد"
    lines = [f"👥 *{quiz.get('name')}*"]
    if quiz.get("description"):
        lines.append(f"_{quiz['description']}_")
    lines += [
        "",
        f"❓ عدد الأسئلة: *{n_q}*",
        time_line,
        "",
        "ℹ️ هذا الاختبار لا يضيف نقاطاً لرصيدك ولا يدخل في لوحة الشرف — "
        "الهدف منه فقط تنظيم توزيع الفرق.",
    ]
    if n_q == 0:
        lines.append("\n⚠️ لا توجد أسئلة في هذا الاختبار بعد.")
        kb = _back_kb("menu_distro")
    else:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ بدء الاختبار", callback_data=f"dz_start_{quiz_id}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="menu_distro")],
        ])
    await _edit(update, "\n".join(lines), kb)


def _question_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("أ", callback_data="dz_ans_a"),
        InlineKeyboardButton("ب", callback_data="dz_ans_b"),
    ]])


def _question_text(quiz: dict, sq: dict, index: int, remaining=None) -> str:
    n = len(quiz["questions"])
    header = f"⏳ الوقت المتبقي:\n*{_fmt_timer(remaining)}*\n\n" if remaining is not None else ""
    return (
        f"{header}"
        f"👥 *{quiz.get('name')}*  —  السؤال {index + 1}/{n}\n\n"
        f"{sq.get('question', '')}\n\n"
        f"أ) {sq.get('option_a', '')}\n"
        f"ب) {sq.get('option_b', '')}"
    )


async def handle_distro_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    user_id = query.from_user.id
    quiz_id = query.data.replace("dz_start_", "")
    quiz    = ds.get_quiz(quiz_id)

    if not quiz or not quiz.get("visible") or not quiz.get("questions"):
        await query.answer("⚠️ هذا الاختبار غير متاح.", show_alert=True)
        return
    if ds.has_taken_quiz(quiz_id, user_id):
        await query.answer("✅ لقد قمت بحل هذا الاختبار مسبقاً.", show_alert=True)
        return
    if not ds.is_entry_open(quiz):
        await query.answer("🚪 الدخول لهذا الاختبار مغلق حالياً.", show_alert=True)
        return

    remaining = ds.remaining_seconds(quiz)
    if remaining is not None and remaining <= 0:
        await query.answer("⏰ انتهى وقت هذا الاختبار.", show_alert=True)
        await _edit(update, EXPIRED_MSG, _back_kb("menu_distro"))
        return

    await query.answer()
    session = ds.start_session(user_id, quiz_id)
    sq = ds.get_shuffled_question(quiz, session, 0)
    await _edit(update, _question_text(quiz, sq, 0, remaining), _question_kb())

    if remaining is not None:
        _schedule_timer(context, user_id, query.message.chat_id, query.message.message_id)


def _fmt_duration_short(seconds) -> str:
    seconds = max(0, int(seconds or 0))
    m, s = divmod(seconds, 60)
    return f"{m} د {s} ث" if m else f"{s} ث"


def _build_results_blocks(quiz: dict, score: int, total_points: int, correct_count: int,
                           wrong_count: int, percentage: float, duration_seconds,
                           review: list) -> list:
    """One 'block' of text per logical piece (summary, then one per question).
    _pack_blocks() below groups these into Telegram-message-sized chunks."""
    blocks = [
        "✅ *انتهى الاختبار*\n\n"
        f"📊 اسم الاختبار: *{quiz.get('name', '—')}*\n"
        f"🏆 الدرجة: *{score}* من *{total_points}*\n"
        f"📈 النسبة المئوية: *{percentage}٪*\n"
        f"✅ الإجابات الصحيحة: *{correct_count}*\n"
        f"❌ الإجابات الخاطئة: *{wrong_count}*\n"
        f"⏱ مدة الحل: *{_fmt_duration_short(duration_seconds)}*\n\n"
        "ℹ️ هذه الدرجة لا تضيف نقاطاً لرصيدك ولا تدخل في لوحة الشرف — "
        "تُستخدم فقط لتنظيم توزيع الفرق."
    ]
    for i, r in enumerate(review, 1):
        chosen  = r.get("chosen")
        correct = r.get("correct")
        chosen_text  = r.get("option_a") if chosen == "a" else r.get("option_b")
        correct_text = r.get("option_a") if correct == "a" else r.get("option_b")
        lines = [
            f"*السؤال {i}:* {r.get('question', '')}",
            f"أ) {r.get('option_a', '')}",
            f"ب) {r.get('option_b', '')}",
            "",
        ]
        if chosen == correct:
            lines.append(f"🟢 إجابتك:\n{chosen_text}")
            lines.append("✅ إجابتك صحيحة")
        else:
            lines.append(f"🔴 إجابتك:\n{chosen_text or '—'}")
            lines.append(f"🟢 الإجابة الصحيحة:\n{correct_text}")
        blocks.append("\n".join(lines))
    return blocks


def _pack_blocks(blocks: list, limit: int = 3500) -> list:
    """Greedily pack blocks into <=`limit`-char chunks (Telegram messages
    cap at 4096 chars; 3500 leaves comfortable headroom)."""
    chunks, current = [], ""
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > limit and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


async def _send_chunks(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        chunks: list, keyboard=None) -> None:
    if not chunks:
        return
    await _edit(update, chunks[0], keyboard if len(chunks) == 1 else None)
    chat_id = update.callback_query.message.chat_id
    for chunk in chunks[1:-1]:
        await context.bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.MARKDOWN)
    if len(chunks) > 1:
        await context.bot.send_message(
            chat_id=chat_id, text=chunks[-1], parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard,
        )


async def _finish_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        user_id: int, quiz: dict, session: dict) -> None:
    _cancel_timer(context, user_id)

    quiz_id       = session["quiz_id"]
    n_q           = len(quiz.get("questions", []))
    correct_count = session.get("correct_count", 0)
    wrong_count   = n_q - correct_count
    points_each   = ds.get_points_per_question(quiz)
    score         = correct_count * points_each
    total_points  = n_q * points_each
    percentage    = round((correct_count / n_q) * 100, 1) if n_q else 0.0

    started_at = session.get("started_at")
    try:
        duration_seconds = (datetime.utcnow() - datetime.fromisoformat(started_at)).total_seconds()
    except Exception:
        duration_seconds = 0

    # Build the per-question review in the participant's OWN shuffled order,
    # using the still-live session dict (question_order/option_swap) before
    # end_session() below removes it from storage.
    review  = []
    answers = session.get("answers", [])
    for pos in range(n_q):
        sq = ds.get_shuffled_question(quiz, session, pos)
        if not sq:
            continue
        review.append({
            "question": sq["question"],
            "option_a": sq["option_a"],
            "option_b": sq["option_b"],
            "chosen":   answers[pos] if pos < len(answers) else None,
            "correct":  sq["correct"],
        })

    user = get_user(user_id) or {}
    # Score only — this NEVER calls credits.add_credits() or transactions.record().
    ds.add_result({
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
        "duration_seconds": int(duration_seconds),
        "review":      review,
    })
    ds.end_session(user_id)

    if ds.is_results_visible(quiz):
        blocks = _build_results_blocks(
            quiz, score, total_points, correct_count, wrong_count,
            percentage, duration_seconds, review,
        )
        await _send_chunks(update, context, _pack_blocks(blocks), _back_kb("menu_distro"))
    else:
        text = (
            "✅ تم استلام إجاباتك بنجاح.\n\n"
            "يرجى انتظار إعلان النتائج من قبل المشرف."
        )
        await _edit(update, text, _back_kb("menu_distro"))


async def _expire_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Time ran out - close the attempt with NO score and NO recorded result."""
    _cancel_timer(context, user_id)
    ds.end_session(user_id)
    await _edit(update, EXPIRED_MSG, _back_kb("menu_distro"))


async def handle_distro_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    user_id = query.from_user.id
    choice  = query.data.replace("dz_ans_", "")  # "a" or "b"

    session = ds.get_session(user_id)
    if not session:
        await query.answer("⚠️ لا يوجد اختبار جارٍ. استخدم /start.", show_alert=True)
        return

    quiz_id = session["quiz_id"]
    quiz    = ds.get_quiz(quiz_id)
    if not quiz or not quiz.get("questions"):
        _cancel_timer(context, user_id)
        ds.end_session(user_id)
        await query.answer("⚠️ حدث خطأ في الاختبار.", show_alert=True)
        return

    await query.answer()

    remaining = ds.remaining_seconds(quiz)
    if remaining is not None and remaining <= 0:
        await _expire_quiz(update, context, user_id)
        return

    questions = quiz["questions"]
    index     = session.get("current_index", 0)
    if index >= len(questions):
        await _finish_quiz(update, context, user_id, quiz, session)
        return

    sq             = ds.get_shuffled_question(quiz, session, index)
    correct_choice = sq.get("correct") if sq else None
    is_correct     = (choice == correct_choice)

    answers = list(session.get("answers", []))
    answers.append(choice)
    correct_count = session.get("correct_count", 0) + (1 if is_correct else 0)
    next_index    = index + 1

    session = ds.update_session(
        user_id, answers=answers, correct_count=correct_count, current_index=next_index,
    )

    if next_index >= len(questions):
        await _finish_quiz(update, context, user_id, quiz, session)
        return

    sq_next = ds.get_shuffled_question(quiz, session, next_index)
    await _edit(update, _question_text(quiz, sq_next, next_index, remaining), _question_kb())
    if remaining is not None:
        _schedule_timer(context, user_id, query.message.chat_id, query.message.message_id)
