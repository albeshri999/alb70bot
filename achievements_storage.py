# -*- coding: utf-8 -*-
"""
Independent storage layer for '🏅 نظام الإنجازات' (Achievements System).

Fully self-contained — reads other systems' data (quiz_storage,
initiatives_storage) ONLY to decide who earned a badge, and never writes
anything back into those systems' files. All badge data lives in its own
file:
  - data/achievements.json → { "<user_id>": [ {badge entry}, ... ] }

Designed to be easy to extend: adding a new badge type only means adding an
entry to BADGE_DEFS and writing a small function that calls award_badge()/
revoke_badge() at the right moment — no changes needed to the storage
functions below or to the display code in achievements_user.py.
"""
import os
import json
from datetime import datetime

ACHIEVEMENTS_FILE = "data/achievements.json"

# ── Badge type registry (extend this dict to add new badge types) ──────────
# Each key is a *badge_type* used as (part of) a badge's unique key.
BADGE_DEFS = {
    "fastest_1":            {"icon": "🥇", "name": "أسرع حل"},
    "fastest_2":            {"icon": "🥈", "name": "ثاني أسرع حل"},
    "fastest_3":            {"icon": "🥉", "name": "ثالث أسرع حل"},
    "first_initiative":     {"icon": "💡", "name": "أول مبادر"},
    "active_initiator":     {"icon": "🚀", "name": "مبادر نشيط"},
    "outstanding_initiator": {"icon": "🌟", "name": "مبادر متميز"},
    # 🎭 Submissions (talent-contest) badges — awarded via
    # achievements_storage.award_submission_badges(), called from
    # submissions_admin.py right after a contest's results are finalized.
    "submission_rank_1":    {"icon": "🥇", "name": "المركز الأول"},
    "submission_rank_2":    {"icon": "🥈", "name": "المركز الثاني"},
    "submission_rank_3":    {"icon": "🥉", "name": "المركز الثالث"},
    "theme_reciter":        {"icon": "📖", "name": "أفضل قارئ"},
    "theme_muadhin":        {"icon": "🕌", "name": "أفضل مؤذن"},
    "theme_munshid":        {"icon": "🎤", "name": "أفضل منشِد"},
    "theme_speaker":        {"icon": "🎙", "name": "أفضل متحدث"},
    "theme_photographer":   {"icon": "📷", "name": "أفضل مصور"},
    # Examples of future badges — just add a line here, no other code needed
    # to store/display them:
    # "season_champion":    {"icon": "🏆", "name": "بطل الموسم"},
    # "perfect_score":      {"icon": "⭐️", "name": "العلامة الكاملة"},
    # "five_quizzes":       {"icon": "📚", "name": "أكمل 5 اختبارات"},
    # "five_initiatives":   {"icon": "🎯", "name": "نفذ 5 مبادرات"},
}

# Keyword → theme-badge-type lookup for submission names (extend this list
# to recognize new contest themes without touching any other code — the
# first matching keyword wins).
SUBMISSION_THEME_KEYWORDS = [
    (("قرآن", "تلاوة"),      "theme_reciter"),
    (("أذان",),               "theme_muadhin"),
    (("نشيد", "أناشيد"),      "theme_munshid"),
    (("تعبير", "متحدث", "حديث"), "theme_speaker"),
    (("تصوير", "صورة"),       "theme_photographer"),
]


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


def load_all() -> dict:
    return _load_json(ACHIEVEMENTS_FILE, {})


def save_all(data: dict) -> None:
    _save_json(ACHIEVEMENTS_FILE, data)


def get_user_badges(user_id) -> list:
    return load_all().get(str(user_id), [])


def has_badge(user_id, key: str) -> bool:
    return any(b.get("key") == key for b in get_user_badges(user_id))


def badge_awarded_to_anyone(key: str) -> bool:
    data = load_all()
    return any(any(b.get("key") == key for b in badges) for badges in data.values())


def award_badge(user_id, key: str, badge_type: str, reason: str = "") -> bool:
    """Award a badge to a user, identified by a globally-unique `key`
    (e.g. 'fastest_1_q3' for a per-quiz badge, or a plain type name for a
    one-off global badge like 'first_initiative'). No-op if this user
    already has it. Returns True if newly awarded."""
    defn = BADGE_DEFS.get(badge_type, {"icon": "🏅", "name": badge_type})
    data = load_all()
    uid = str(user_id)
    badges = data.setdefault(uid, [])
    if any(b.get("key") == key for b in badges):
        return False
    badges.append({
        "key": key,
        "type": badge_type,
        "icon": defn["icon"],
        "name": defn["name"],
        "description": reason or defn["name"],
        "awarded_at": datetime.utcnow().isoformat(),
        "reason": reason or "",
    })
    save_all(data)
    return True


def revoke_badge_everywhere(key: str) -> None:
    """Remove a badge (by its unique key) from whichever user currently
    holds it — used when a ranked badge (e.g. 'fastest_1' for a given
    quiz) needs to move to a new holder after a recomputation."""
    data = load_all()
    changed = False
    for uid, badges in list(data.items()):
        new_badges = [b for b in badges if b.get("key") != key]
        if len(new_badges) != len(badges):
            data[uid] = new_badges
            changed = True
    if changed:
        save_all(data)


def revoke_badge(user_id, key: str) -> None:
    data = load_all()
    uid = str(user_id)
    if uid in data:
        new_badges = [b for b in data[uid] if b.get("key") != key]
        if len(new_badges) != len(data[uid]):
            data[uid] = new_badges
            save_all(data)


# ── Badge-award logic (the extensible "hooks" other modules call) ──────────

def recompute_quiz_speed_badges(quiz_id) -> None:
    """Recompute the top-3-fastest badges for one quiz. Safe to call every
    time a new attempt finishes — it always reflects the current top 3
    among each participant's LATEST attempt, moving a badge to a new
    holder if someone faster comes along."""
    try:
        import quiz_storage as qs
    except Exception:
        return
    quiz = qs.get_quiz(quiz_id)
    quiz_name = quiz.get("name", "—") if quiz else "—"
    latest = qs.latest_results_by_user(quiz_id)
    entries = sorted(
        latest.values(),
        key=lambda r: (r.get("duration_seconds") if r.get("duration_seconds") is not None else float("inf")),
    )
    ranks = ["fastest_1", "fastest_2", "fastest_3"]
    for i, badge_type in enumerate(ranks):
        key = f"{badge_type}_q{quiz_id}"
        revoke_badge_everywhere(key)
        if i < len(entries):
            uid = entries[i].get("user_id")
            if uid is not None:
                award_badge(uid, key, badge_type, f"في اختبار: {quiz_name}")


def check_first_initiative_badge(user_id) -> None:
    """Award '💡 أول مبادر' once, globally, to the first person whose
    initiative-execution request is ever accepted. No-op afterwards."""
    if badge_awarded_to_anyone("first_initiative"):
        return
    award_badge(user_id, "first_initiative", "first_initiative", "أول مبادر يُقبل طلبه")


def check_initiative_completion_badges(user_id) -> None:
    """Award '🚀 مبادر نشيط' after 2 completed initiatives and '🌟 مبادر
    متميز' after 3, per user. Called after an initiative is marked
    completed for that user."""
    try:
        import initiatives_storage as ins
    except Exception:
        return
    count = ins.count_completed_for_user(user_id)
    if count >= 2:
        award_badge(user_id, "active_initiator", "active_initiator", "بعد تنفيذ مبادرتين")
    if count >= 3:
        award_badge(user_id, "outstanding_initiator", "outstanding_initiator", "بعد تنفيذ ثلاث مبادرات")


def _theme_badge_for(submission_name: str):
    name = submission_name or ""
    for keywords, badge_type in SUBMISSION_THEME_KEYWORDS:
        if any(k in name for k in keywords):
            return badge_type
    return None


def award_submission_badges(submission_id, submission_name: str, winners: list) -> None:
    """Called once, right after a '🎭 مشاركة' contest's results are
    finalized. `winners` is the ordered list of winning entries (each a
    dict with 'user_id' and 'rank', rank 1 = first place). Awards the
    🥇/🥈/🥉 rank badges (ranks 1-3 only) plus, to the 1st-place winner
    only, a theme badge if the contest's name matches a known keyword
    (see SUBMISSION_THEME_KEYWORDS) — e.g. 'أفضل تلاوة القرآن' → 📖 أفضل قارئ."""
    rank_types = {1: "submission_rank_1", 2: "submission_rank_2", 3: "submission_rank_3"}
    for w in winners:
        rank = w.get("rank")
        if rank in rank_types:
            key = f"{rank_types[rank]}_s{submission_id}"
            award_badge(w.get("user_id"), key, rank_types[rank], f"في مشاركة: {submission_name}")
        if rank == 1:
            theme_type = _theme_badge_for(submission_name)
            if theme_type:
                theme_key = f"{theme_type}_s{submission_id}"
                award_badge(w.get("user_id"), theme_key, theme_type, f"في مشاركة: {submission_name}")
