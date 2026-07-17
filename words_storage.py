# -*- coding: utf-8 -*-
"""
Independent storage layer for '📖 إدارة الكلمات' (Word-Delivery System).

Fully self-contained — does NOT read or write any file belonging to the
word-*competition* system (days.json/users.json stages), the quiz system,
the team-distribution system, or the initiatives/submissions systems.
Point awards go through the existing credits.py helper (called from
words_admin.py), exactly like the other independent modules do.

All data lives in its own JSON files under data/:
  - data/words.json             → word texts, grouped by competition day
  - data/word_announcements.json→ which day(s) currently have an open
                                   "🎤 إلقاء الكلمات" announcement
  - data/word_volunteers.json   → participants who asked to deliver a word,
                                   grouped by day, with their status
  - data/word_delivered.json    → global list of user_ids who have already
                                   delivered a word (one-time per competition)
"""
import json
import os
import random
from datetime import datetime

WORDS_FILE        = "data/words.json"
ANNOUNCEMENTS_FILE = "data/word_announcements.json"
VOLUNTEERS_FILE    = "data/word_volunteers.json"
DELIVERED_FILE     = "data/word_delivered.json"

STATUS_AVAILABLE = "available"
STATUS_RESERVED  = "reserved"
STATUS_USED      = "used"

VOL_WAITING  = "waiting"
VOL_ASSIGNED = "assigned"
VOL_DONE     = "done"


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


# ── Words ─────────────────────────────────────────────────────────────────────

def load_words() -> dict:
    return _load_json(WORDS_FILE, {})


def save_words(data: dict) -> None:
    _save_json(WORDS_FILE, data)


def words_for_day(day_key) -> list:
    return load_words().get(str(day_key), [])


def _next_word_id(day_words: list) -> str:
    nums = [int(w["id"]) for w in day_words if str(w.get("id", "")).isdigit()]
    return str((max(nums) + 1) if nums else 1)


def add_word(day_key: str, text: str, points: int) -> dict:
    words = load_words()
    key = str(day_key)
    day_words = words.setdefault(key, [])
    entry = {
        "id": _next_word_id(day_words),
        "text": text,
        "points": int(points),
        "status": STATUS_AVAILABLE,
        "reserved_by": None,
        "created_at": datetime.utcnow().isoformat(),
    }
    day_words.append(entry)
    save_words(words)
    return entry


def get_word(day_key, word_id) -> dict:
    for w in words_for_day(day_key):
        if str(w.get("id")) == str(word_id):
            return w
    return {}


def update_word(day_key, word_id, **fields) -> None:
    words = load_words()
    key = str(day_key)
    for w in words.get(key, []):
        if str(w.get("id")) == str(word_id):
            w.update(fields)
            break
    save_words(words)


def delete_word(day_key, word_id) -> bool:
    words = load_words()
    key = str(day_key)
    day_words = words.get(key, [])
    new_words = [w for w in day_words if str(w.get("id")) != str(word_id)]
    removed = len(new_words) != len(day_words)
    if removed:
        words[key] = new_words
        save_words(words)
    return removed


def available_words(day_key) -> list:
    return [w for w in words_for_day(day_key) if w.get("status") == STATUS_AVAILABLE]


def word_counts(day_key) -> dict:
    day_words = words_for_day(day_key)
    return {
        "total":     len(day_words),
        "available": sum(1 for w in day_words if w.get("status") == STATUS_AVAILABLE),
        "reserved":  sum(1 for w in day_words if w.get("status") == STATUS_RESERVED),
        "used":      sum(1 for w in day_words if w.get("status") == STATUS_USED),
    }


def pick_random_available_word(day_key):
    pool = available_words(day_key)
    return random.choice(pool) if pool else None


# ── Announcements ─────────────────────────────────────────────────────────────

def load_announcements() -> dict:
    return _load_json(ANNOUNCEMENTS_FILE, {})


def save_announcements(data: dict) -> None:
    _save_json(ANNOUNCEMENTS_FILE, data)


def is_announcement_open(day_key) -> bool:
    return bool(load_announcements().get(str(day_key), {}).get("open"))


def open_announcement(day_key) -> None:
    data = load_announcements()
    data[str(day_key)] = {"open": True, "opened_at": datetime.utcnow().isoformat()}
    save_announcements(data)


def close_announcement(day_key) -> None:
    data = load_announcements()
    key = str(day_key)
    if key in data:
        data[key]["open"] = False
        data[key]["closed_at"] = datetime.utcnow().isoformat()
    else:
        data[key] = {"open": False}
    save_announcements(data)


def open_day_keys() -> list:
    return [k for k, v in load_announcements().items() if v.get("open")]


def any_announcement_open() -> bool:
    return len(open_day_keys()) > 0


# ── Volunteers ────────────────────────────────────────────────────────────────

def load_volunteers() -> dict:
    return _load_json(VOLUNTEERS_FILE, {})


def save_volunteers(data: dict) -> None:
    _save_json(VOLUNTEERS_FILE, data)


def volunteers_for_day(day_key) -> list:
    return load_volunteers().get(str(day_key), [])


def get_volunteer(day_key, user_id):
    uid = str(user_id)
    for v in volunteers_for_day(day_key):
        if str(v.get("user_id")) == uid:
            return v
    return None


def add_volunteer(day_key, user_id, user_name: str) -> dict:
    data = load_volunteers()
    key = str(day_key)
    day_vols = data.setdefault(key, [])
    entry = {
        "user_id": str(user_id),
        "user_name": user_name or "—",
        "status": VOL_WAITING,
        "assigned_word_id": None,
        "requested_at": datetime.utcnow().isoformat(),
    }
    day_vols.append(entry)
    save_volunteers(data)
    return entry


def waiting_volunteers(day_key) -> list:
    return [v for v in volunteers_for_day(day_key) if v.get("status") == VOL_WAITING]


def set_volunteer_status(day_key, user_id, status: str, **extra) -> None:
    data = load_volunteers()
    key = str(day_key)
    uid = str(user_id)
    for v in data.get(key, []):
        if str(v.get("user_id")) == uid:
            v["status"] = status
            v.update(extra)
            break
    save_volunteers(data)


def user_has_open_volunteer_entry(user_id) -> bool:
    """True if the participant has a waiting/assigned entry on ANY day."""
    uid = str(user_id)
    for day_vols in load_volunteers().values():
        for v in day_vols:
            if str(v.get("user_id")) == uid and v.get("status") in (VOL_WAITING, VOL_ASSIGNED):
                return True
    return False


def find_assigned_volunteer(day_key, user_id):
    return get_volunteer(day_key, user_id)


# ── Delivered (global, one-time participation) ─────────────────────────────────

def load_delivered() -> list:
    return _load_json(DELIVERED_FILE, [])


def save_delivered(data: list) -> None:
    _save_json(DELIVERED_FILE, data)


def has_delivered(user_id) -> bool:
    return str(user_id) in load_delivered()


def mark_delivered(user_id) -> None:
    data = load_delivered()
    uid = str(user_id)
    if uid not in data:
        data.append(uid)
        save_delivered(data)


# ── Assignment (reserve word ⇄ volunteer) ───────────────────────────────────────

def assign_word_to_volunteer(day_key, user_id, word_id) -> None:
    update_word(day_key, word_id, status=STATUS_RESERVED, reserved_by=str(user_id))
    set_volunteer_status(day_key, user_id, VOL_ASSIGNED, assigned_word_id=str(word_id))


def confirm_delivery(day_key, user_id, points_awarded: int) -> bool:
    """Mark the word used, the volunteer done, and record global delivery.
    Returns True if this was the last available word for the day (caller
    should auto-close the announcement)."""
    vol = get_volunteer(day_key, user_id)
    word_id = vol.get("assigned_word_id") if vol else None
    if word_id:
        update_word(day_key, word_id, status=STATUS_USED)
    set_volunteer_status(day_key, user_id, VOL_DONE)
    mark_delivered(user_id)
    return len(available_words(day_key)) == 0


def cancel_delivery(day_key, user_id) -> None:
    """Undo an assignment: the word returns to available and the
    volunteer goes back to waiting (they may be picked again)."""
    vol = get_volunteer(day_key, user_id)
    word_id = vol.get("assigned_word_id") if vol else None
    if word_id:
        update_word(day_key, word_id, status=STATUS_AVAILABLE, reserved_by=None)
    set_volunteer_status(day_key, user_id, VOL_WAITING, assigned_word_id=None)


# ── Statistics ──────────────────────────────────────────────────────────────────

def stats_for_day(day_key) -> dict:
    counts = word_counts(day_key)
    vols = volunteers_for_day(day_key)
    counts["volunteers"] = len(vols)
    counts["delivered"] = sum(1 for v in vols if v.get("status") == VOL_DONE)
    return counts
