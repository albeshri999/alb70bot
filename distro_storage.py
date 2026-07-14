# -*- coding: utf-8 -*-
"""
Independent storage layer for '👥 اختبار توزيع الفرق' (Team-Distribution Test).

Completely self-contained — does NOT read or write any file belonging to the
word-competition system (days.json, users.json, config.json, credit_log.json,
transactions.json, recharge_codes.json, admin_log.json) NOR the existing
quiz system (quizzes.json, quiz_results.json, quiz_sessions.json,
quiz_credit_log.json).

Purpose: this test never touches the credit/balance system and never
appears on the leaderboard — its only output is a raw score per
participant, used solely to build balanced teams (see compute_teams()).

All data lives in its own JSON files under data/:
  - data/distro_quizzes.json  → all distribution-tests + their questions
  - data/distro_results.json  → every finished attempt (score only)
  - data/distro_sessions.json → in-progress attempts (per user)
  - data/distro_teams.json    → last computed team split per test, so
                                 "إعادة التقسيم" / "تصدير الفرق" never need
                                 participants to redo anything
"""
import json
import os
import random
from datetime import datetime

DISTRO_QUIZZES_FILE  = "data/distro_quizzes.json"
DISTRO_RESULTS_FILE  = "data/distro_results.json"
DISTRO_SESSIONS_FILE = "data/distro_sessions.json"
DISTRO_TEAMS_FILE    = "data/distro_teams.json"

# Masculine ordinals for team names ("الفريق الأول", "الفريق الثاني", …).
# Kept local to this feature — utils.py's ARABIC_ORDINALS are feminine
# (made for كلمة/مرحلة) and are not reused here on purpose.
TEAM_ORDINALS = [
    "الأول", "الثاني", "الثالث", "الرابع", "الخامس",
    "السادس", "السابع", "الثامن", "التاسع", "العاشر",
    "الحادي عشر", "الثاني عشر", "الثالث عشر", "الرابع عشر", "الخامس عشر",
    "السادس عشر", "السابع عشر", "الثامن عشر", "التاسع عشر", "العشرون",
]


def team_name(index: int) -> str:
    if index < len(TEAM_ORDINALS):
        return f"الفريق {TEAM_ORDINALS[index]}"
    return f"الفريق {index + 1}"


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


# ── Quizzes (distribution tests) ─────────────────────────────────────────────

def load_quizzes() -> dict:
    return _load_json(DISTRO_QUIZZES_FILE, {})


def save_quizzes(quizzes: dict) -> None:
    _save_json(DISTRO_QUIZZES_FILE, quizzes)


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
        # "الدخول" is independent of "visible": a hidden test never appears
        # to participants at all, while a visible-but-closed test still
        # appears in the list but blocks starting a new attempt.
        "entry_open": True,
        # Countdown clock (timed tests) is anchored to the moment the test
        # becomes visible — same convention as the existing quiz system, so
        # every participant shares the same deadline.
        "opened_at": datetime.utcnow().isoformat() if visible else None,
        "questions": [],
        "created_at": datetime.utcnow().isoformat(),
    })
    return quiz_id


def update_quiz_field(quiz_id, **fields) -> None:
    quiz = get_quiz(quiz_id)
    if quiz:
        quiz.update(fields)
        save_quiz(quiz_id, quiz)


def set_quiz_visible(quiz_id, visible: bool) -> None:
    quiz = get_quiz(quiz_id)
    if not quiz:
        return
    quiz["visible"] = bool(visible)
    if visible:
        quiz["opened_at"] = datetime.utcnow().isoformat()
    save_quiz(quiz_id, quiz)


def remaining_seconds(quiz: dict):
    """Seconds left until a timed test closes. None if the test isn't timed."""
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
        results = load_results()
        new_results = [r for r in results if str(r.get("quiz_id")) != key]
        if len(new_results) != len(results):
            save_results(new_results)
        sessions = load_sessions()
        new_sessions = {u: s for u, s in sessions.items() if str(s.get("quiz_id")) != key}
        if len(new_sessions) != len(sessions):
            save_sessions(new_sessions)
        teams = load_teams()
        if key in teams:
            del teams[key]
            save_teams(teams)
        return True
    return False


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


def set_entry_open(quiz_id, open_: bool) -> None:
    update_quiz_field(quiz_id, entry_open=bool(open_))


def is_entry_open(quiz: dict) -> bool:
    """Defaults to True so older records (before this field existed) keep working."""
    return bool(quiz.get("entry_open", True))


def visible_quizzes() -> dict:
    return {k: v for k, v in load_quizzes().items() if v.get("visible")}


def has_visible_quizzes() -> bool:
    return len(visible_quizzes()) > 0


# ── Results (score only — never touches credits/balance/leaderboard) ────────

def load_results() -> list:
    return _load_json(DISTRO_RESULTS_FILE, [])


def save_results(results: list) -> None:
    _save_json(DISTRO_RESULTS_FILE, results)


def add_result(entry: dict) -> None:
    results = load_results()
    results.append(entry)
    save_results(results)


def results_for_quiz(quiz_id) -> list:
    return [r for r in load_results() if str(r.get("quiz_id")) == str(quiz_id)]


def has_taken_quiz(quiz_id, user_id) -> bool:
    """Retakes are never allowed for this test — one honest attempt per
    participant keeps team-balancing fair."""
    return any(str(r.get("user_id")) == str(user_id) for r in results_for_quiz(quiz_id))


# ── Active sessions (in-progress attempts) ───────────────────────────────────

def load_sessions() -> dict:
    return _load_json(DISTRO_SESSIONS_FILE, {})


def save_sessions(sessions: dict) -> None:
    _save_json(DISTRO_SESSIONS_FILE, sessions)


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


# ── Team splits (snake-draft balancing) ──────────────────────────────────────

def load_teams() -> dict:
    return _load_json(DISTRO_TEAMS_FILE, {})


def save_teams(data: dict) -> None:
    _save_json(DISTRO_TEAMS_FILE, data)


def get_team_split(quiz_id):
    return load_teams().get(str(quiz_id))


def set_team_split(quiz_id, team_size: int, teams: list) -> None:
    data = load_teams()
    data[str(quiz_id)] = {
        "team_size": team_size,
        "teams": teams,
        "generated_at": datetime.utcnow().isoformat(),
    }
    save_teams(data)


def compute_teams(quiz_id, team_size: int):
    """Rank every participant's attempt from highest to lowest score, then
    deal them out to teams using a snake (boustrophedon) draft: pick 1 goes
    to team 1, pick 2 to team 2, …, the last team of the round picks twice
    in a row, then the order unwinds backwards for the next round. This
    keeps every team's total score as close as possible instead of stacking
    all the strongest participants into a single team.

    The number of teams is derived from the requested `team_size` (members
    per team) and the number of participants: num_teams = round(n / team_size),
    clamped between 1 and n.

    Returns a list of team dicts, or None if nobody has finished this test yet.
    """
    results = results_for_quiz(quiz_id)
    if not results:
        return None

    # One entry per user (retakes are blocked, but guard against old data).
    latest: dict = {}
    for r in results:
        latest[str(r.get("user_id"))] = r
    ranked = sorted(
        latest.values(),
        key=lambda r: (-int(r.get("score", 0)), r.get("finished_at", ""), str(r.get("user_id"))),
    )

    n = len(ranked)
    team_size = max(1, int(team_size))
    num_teams = max(1, min(n, int(n / team_size + 0.5)))

    cycle = list(range(num_teams)) + list(range(num_teams - 1, -1, -1))
    buckets = [[] for _ in range(num_teams)]
    for i, r in enumerate(ranked):
        buckets[cycle[i % len(cycle)]].append(r)

    teams = []
    for i, members in enumerate(buckets):
        total = sum(int(m.get("score", 0)) for m in members)
        avg   = round(total / len(members), 2) if members else 0
        teams.append({
            "name": team_name(i),
            "members": [
                {
                    "user_id": m.get("user_id"),
                    "user_name": m.get("user_name") or "—",
                    "score": int(m.get("score", 0)),
                }
                for m in members
            ],
            "total": total,
            "average": avg,
        })
    return teams
