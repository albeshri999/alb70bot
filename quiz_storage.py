# -*- coding: utf-8 -*-
"""
Independent storage layer for the '📝 إدارة الاختبارات' (Quiz Management) system.

This module is fully self-contained and does NOT read or write any of the
existing word-competition files (days.json, users.json, config.json,
credit_log.json, transactions.json, admin_log.json, recharge_codes.json).

All quiz data lives in its own JSON files under data/:
  - data/quizzes.json       → all quizzes + their questions
  - data/quiz_results.json  → every finished attempt (for admin results view)
  - data/quiz_sessions.json → in-progress attempts (per user)
"""
import json
import os
import random
from datetime import datetime

QUIZZES_FILE          = "data/quizzes.json"
QUIZ_RESULTS_FILE     = "data/quiz_results.json"
QUIZ_SESSIONS_FILE    = "data/quiz_sessions.json"
QUIZ_CREDIT_LOG_FILE  = "data/quiz_credit_log.json"


def _load_json(filepath: str, default):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    if not os.path.exists(filepath):
        return default
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(filepath: str, data) -> None:
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Quizzes ───────────────────────────────────────────────────────────────────

def load_quizzes() -> dict:
    return _load_json(QUIZZES_FILE, {})


def save_quizzes(quizzes: dict) -> None:
    _save_json(QUIZZES_FILE, quizzes)


def get_quiz(quiz_id) -> dict:
    return load_quizzes().get(str(quiz_id), {})


def save_quiz(quiz_id, data: dict) -> None:
    quizzes = load_quizzes()
    quizzes[str(quiz_id)] = data
    save_quizzes(quizzes)


def next_quiz_id() -> str:
    quizzes = load_quizzes()
    nums = [int(k) for k in quizzes.keys() if str(k).isdigit()]
    return str((max(nums) + 1) if nums else 1)


def create_quiz(name: str, description: str, points_per_question: int,
                 timed: bool, time_minutes, visible: bool) -> str:
    quiz_id = next_quiz_id()
    save_quiz(quiz_id, {
        "id": quiz_id,
        "name": name,
        "description": description or "",
        "points_per_question": points_per_question,
        "timed": bool(timed),
        "time_minutes": time_minutes if timed else None,
        "visible": bool(visible),
        # The countdown clock (for timed quizzes) is anchored to the moment
        # the quiz becomes visible to participants — NOT to when a given
        # participant taps "بدء الاختبار". See set_quiz_visible() below.
        "opened_at": datetime.utcnow().isoformat() if visible else None,
        "show_score": True,
        "allow_retake": False,
        "questions": [],
        "created_at": datetime.utcnow().isoformat(),
    })
    return quiz_id


def set_quiz_visible(quiz_id, visible: bool) -> None:
    """Show/hide a quiz. Turning it ON (re)starts its countdown clock — this
    is the single moment 'opened_at' is stamped, so every participant shares
    the same deadline regardless of when they personally open the quiz."""
    quiz = get_quiz(quiz_id)
    if not quiz:
        return
    quiz["visible"] = bool(visible)
    if visible:
        quiz["opened_at"] = datetime.utcnow().isoformat()
    save_quiz(quiz_id, quiz)


def remaining_seconds(quiz: dict):
    """Seconds left until a timed quiz closes, based on 'opened_at' + time_minutes.
    Returns None if the quiz isn't timed (untimed quizzes are unaffected —
    they keep working exactly as before)."""
    if not quiz or not quiz.get("timed"):
        return None
    opened_at = quiz.get("opened_at")
    minutes   = quiz.get("time_minutes") or 0
    if not opened_at:
        return None
    try:
        started = datetime.fromisoformat(opened_at)
    except Exception:
        return None
    deadline = started.timestamp() + minutes * 60
    return int(deadline - datetime.utcnow().timestamp())


def delete_quiz(quiz_id) -> bool:
    quizzes = load_quizzes()
    key = str(quiz_id)
    if key in quizzes:
        del quizzes[key]
        save_quizzes(quizzes)
        results = load_quiz_results()
        new_results = [r for r in results if str(r.get("quiz_id")) != key]
        if len(new_results) != len(results):
            save_quiz_results(new_results)
        sessions = load_sessions()
        new_sessions = {u: s for u, s in sessions.items() if str(s.get("quiz_id")) != key}
        if len(new_sessions) != len(sessions):
            save_sessions(new_sessions)
        credit_log = load_credit_log()
        if key in credit_log:
            del credit_log[key]
            save_credit_log(credit_log)
        return True
    return False


def update_quiz_field(quiz_id, **fields) -> None:
    quiz = get_quiz(quiz_id)
    if quiz:
        quiz.update(fields)
        save_quiz(quiz_id, quiz)


def is_results_visible(quiz: dict) -> bool:
    """Whether a participant sees their own score/percentage/answer-review
    after finishing. The admin's own results view is never affected by this."""
    return bool(quiz.get("show_score", True))


def add_question(quiz_id, question: str, option_a: str, option_b: str, correct: str) -> None:
    quiz = get_quiz(quiz_id)
    questions = quiz.get("questions", [])
    questions.append({
        "question": question,
        "option_a": option_a,
        "option_b": option_b,
        "correct": correct,  # "a" or "b"
    })
    quiz["questions"] = questions
    save_quiz(quiz_id, quiz)


def update_question(quiz_id, index: int, **fields) -> bool:
    quiz = get_quiz(quiz_id)
    questions = quiz.get("questions", [])
    if 0 <= index < len(questions):
        questions[index].update(fields)
        quiz["questions"] = questions
        save_quiz(quiz_id, quiz)
        return True
    return False


def delete_question(quiz_id, index: int) -> bool:
    quiz = get_quiz(quiz_id)
    questions = quiz.get("questions", [])
    if 0 <= index < len(questions):
        questions.pop(index)
        quiz["questions"] = questions
        save_quiz(quiz_id, quiz)
        return True
    return False


def visible_quizzes() -> dict:
    return {k: v for k, v in load_quizzes().items() if v.get("visible")}


def has_visible_quizzes() -> bool:
    return len(visible_quizzes()) > 0


# ── Results ───────────────────────────────────────────────────────────────────

def load_quiz_results() -> list:
    return _load_json(QUIZ_RESULTS_FILE, [])


def save_quiz_results(results: list) -> None:
    _save_json(QUIZ_RESULTS_FILE, results)


def add_quiz_result(entry: dict) -> None:
    results = load_quiz_results()
    results.append(entry)
    save_quiz_results(results)


def results_for_quiz(quiz_id) -> list:
    return [r for r in load_quiz_results() if str(r.get("quiz_id")) == str(quiz_id)]


def has_taken_quiz(quiz_id, user_id) -> bool:
    return any(str(r.get("user_id")) == str(user_id) for r in results_for_quiz(quiz_id))


def delete_results_for_user(quiz_id, user_id) -> int:
    """Remove every finished-attempt entry for this one participant on this
    quiz (score, answers, timing, percentage — the whole result record).
    Returns how many entries were removed. Since has_taken_quiz() reads from
    this same list, removing them naturally lets the participant retake the
    quiz from scratch (subject to the quiz's normal allow_retake rule)."""
    results  = load_quiz_results()
    key, uid = str(quiz_id), str(user_id)
    kept     = [r for r in results if not (str(r.get("quiz_id")) == key and str(r.get("user_id")) == uid)]
    removed  = len(results) - len(kept)
    if removed:
        save_quiz_results(kept)
    return removed


def delete_all_results(quiz_id) -> int:
    """Remove every finished-attempt entry for ALL participants on this quiz."""
    results = load_quiz_results()
    key     = str(quiz_id)
    kept    = [r for r in results if str(r.get("quiz_id")) != key]
    removed = len(results) - len(kept)
    if removed:
        save_quiz_results(kept)
    return removed


def latest_results_by_user(quiz_id) -> dict:
    """Map user_id -> that user's most recent finished attempt for this quiz.

    Every finished attempt is appended to QUIZ_RESULTS_FILE in chronological
    order (see quiz_user._finish_quiz), so the last entry seen for a given
    user while scanning the list is always their latest attempt — this is
    what lets retakes be resolved to 'the latest attempt only', per the
    quiz-results-crediting feature.
    """
    latest: dict = {}
    for r in results_for_quiz(quiz_id):
        latest[str(r.get("user_id"))] = r
    return latest


# ── Results-crediting log (which attempt's points were already added to a
# participant's balance) ─────────────────────────────────────────────────────
# Kept in its own file, independent of everything else, so that recalculating
# a quiz's results never touches days.json/users.json/credit_log.json/etc.
# directly — only quiz_admin.py reads this to decide what to add/remove via
# the existing credits.add_credits()/transactions.record() functions.

def load_credit_log() -> dict:
    return _load_json(QUIZ_CREDIT_LOG_FILE, {})


def save_credit_log(data: dict) -> None:
    _save_json(QUIZ_CREDIT_LOG_FILE, data)


def get_credited_entry(quiz_id, user_id):
    """Return {'score': int, 'finished_at': str} for the last attempt of this
    user+quiz that had its points credited to the balance, or None."""
    return load_credit_log().get(str(quiz_id), {}).get(str(user_id))


def set_credited_entry(quiz_id, user_id, score: int, finished_at) -> None:
    log = load_credit_log()
    quiz_log = log.setdefault(str(quiz_id), {})
    quiz_log[str(user_id)] = {"score": score, "finished_at": finished_at}
    save_credit_log(log)


def clear_credited_entry(quiz_id, user_id) -> None:
    """Forget that this user's score for this quiz was ever credited — used
    when an admin deletes their result, right after any already-credited
    points have been removed from their balance (see quiz_admin.py)."""
    log = load_credit_log()
    quiz_log = log.get(str(quiz_id))
    if quiz_log and str(user_id) in quiz_log:
        del quiz_log[str(user_id)]
        save_credit_log(log)


def clear_all_credited_entries(quiz_id) -> None:
    """Forget the entire credited-log for this quiz — used when an admin
    deletes ALL of this quiz's results."""
    log = load_credit_log()
    key = str(quiz_id)
    if key in log:
        del log[key]
        save_credit_log(log)


# ── Active sessions (in-progress attempts) ───────────────────────────────────

def load_sessions() -> dict:
    return _load_json(QUIZ_SESSIONS_FILE, {})


def save_sessions(sessions: dict) -> None:
    _save_json(QUIZ_SESSIONS_FILE, sessions)


def get_session(user_id) -> dict:
    return load_sessions().get(str(user_id), {})


def start_session(user_id, quiz_id) -> dict:
    """Start a new attempt. Each participant gets their own independent
    randomized question order ('question_order') and, per question, an
    independent coin-flip on whether its two options are swapped
    ('option_swap') — see get_shuffled_question() below. Both are generated
    once here and stay fixed for the rest of this attempt."""
    quiz      = get_quiz(quiz_id)
    n         = len(quiz.get("questions", []))
    order     = list(range(n))
    random.shuffle(order)
    swap      = {str(i): random.choice([True, False]) for i in range(n)}

    sessions = load_sessions()
    session = {
        "quiz_id": str(quiz_id),
        "current_index": 0,
        "correct_count": 0,
        "answers": [],
        "question_order": order,
        "option_swap": swap,
        "started_at": datetime.utcnow().isoformat(),
    }
    sessions[str(user_id)] = session
    save_sessions(sessions)
    return session


def get_shuffled_question(quiz: dict, session: dict, position: int):
    """Return the question this PARTICIPANT sees at position `position` of
    their attempt (0-based), with 'option_a'/'option_b'/'correct' already
    adjusted for their personal per-question option swap. Returns None if
    `position` is past the end. Scoring is unaffected because 'correct'
    here always reflects the CURRENTLY DISPLAYED option lettering."""
    questions = quiz.get("questions", [])
    order = session.get("question_order") or list(range(len(questions)))
    if position < 0 or position >= len(order):
        return None
    orig_index = order[position]
    if orig_index >= len(questions):
        return None
    q = questions[orig_index]
    swap = bool((session.get("option_swap") or {}).get(str(orig_index), False))
    if swap:
        option_a = q.get("option_b", "")
        option_b = q.get("option_a", "")
        correct  = "a" if q.get("correct") == "b" else "b"
    else:
        option_a = q.get("option_a", "")
        option_b = q.get("option_b", "")
        correct  = q.get("correct")
    return {
        "question":    q.get("question", ""),
        "option_a":    option_a,
        "option_b":    option_b,
        "correct":     correct,
        "orig_index":  orig_index,
    }


def update_session(user_id, **fields) -> dict:
    sessions = load_sessions()
    key = str(user_id)
    session = sessions.get(key, {})
    session.update(fields)
    sessions[key] = session
    save_sessions(sessions)
    return session


def end_session(user_id) -> None:
    sessions = load_sessions()
    key = str(user_id)
    if key in sessions:
        del sessions[key]
        save_sessions(sessions)
