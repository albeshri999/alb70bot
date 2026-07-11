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
from datetime import datetime

QUIZZES_FILE       = "data/quizzes.json"
QUIZ_RESULTS_FILE  = "data/quiz_results.json"
QUIZ_SESSIONS_FILE = "data/quiz_sessions.json"


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
        "show_score": True,
        "allow_retake": False,
        "questions": [],
        "created_at": datetime.utcnow().isoformat(),
    })
    return quiz_id


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
        return True
    return False


def update_quiz_field(quiz_id, **fields) -> None:
    quiz = get_quiz(quiz_id)
    if quiz:
        quiz.update(fields)
        save_quiz(quiz_id, quiz)


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


# ── Active sessions (in-progress attempts) ───────────────────────────────────

def load_sessions() -> dict:
    return _load_json(QUIZ_SESSIONS_FILE, {})


def save_sessions(sessions: dict) -> None:
    _save_json(QUIZ_SESSIONS_FILE, sessions)


def get_session(user_id) -> dict:
    return load_sessions().get(str(user_id), {})


def start_session(user_id, quiz_id) -> dict:
    sessions = load_sessions()
    session = {
        "quiz_id": str(quiz_id),
        "current_index": 0,
        "correct_count": 0,
        "answers": [],
        "started_at": datetime.utcnow().isoformat(),
    }
    sessions[str(user_id)] = session
    save_sessions(sessions)
    return session


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
