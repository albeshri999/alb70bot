# -*- coding: utf-8 -*-
"""
Independent storage layer for '💡 نظام المبادرات' (Initiatives System).

Fully self-contained — does NOT read or write any file belonging to the
word-competition system, the quiz system, or the team-distribution system.
Balance changes go through the existing credits.py/transactions.py helpers
(called from initiatives_admin.py), exactly like the quiz-crediting flow.

All data lives in its own JSON files under data/:
  - data/initiatives.json          → initiative definitions
  - data/initiative_requests.json  → every execution request (one per
                                      user per initiative) and its status
"""
import json
import os
from datetime import datetime

INITIATIVES_FILE = "data/initiatives.json"
REQUESTS_FILE    = "data/initiative_requests.json"

STATUS_PENDING   = "pending"
STATUS_ACCEPTED  = "accepted"
STATUS_REJECTED  = "rejected"
STATUS_COMPLETED = "completed"


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


# ── Initiatives (definitions) ────────────────────────────────────────────────

def load_initiatives() -> dict:
    return _load_json(INITIATIVES_FILE, {})


def save_initiatives(data: dict) -> None:
    _save_json(INITIATIVES_FILE, data)


def get_initiative(initiative_id) -> dict:
    return load_initiatives().get(str(initiative_id), {})


def save_initiative(initiative_id, data: dict) -> None:
    initiatives = load_initiatives()
    initiatives[str(initiative_id)] = data
    save_initiatives(initiatives)


def next_initiative_id() -> str:
    initiatives = load_initiatives()
    nums = [int(k) for k in initiatives.keys() if str(k).isdigit()]
    return str((max(nums) + 1) if nums else 1)


def create_initiative(name: str, description: str, points: int, visible: bool) -> str:
    initiative_id = next_initiative_id()
    save_initiative(initiative_id, {
        "id": initiative_id,
        "name": name,
        "description": description or "",
        "points": int(points),
        "visible": bool(visible),
        "created_at": datetime.utcnow().isoformat(),
    })
    return initiative_id


def update_initiative_field(initiative_id, **fields) -> None:
    initiative = get_initiative(initiative_id)
    if initiative:
        initiative.update(fields)
        save_initiative(initiative_id, initiative)


def set_initiative_visible(initiative_id, visible: bool) -> None:
    update_initiative_field(initiative_id, visible=bool(visible))


def delete_initiative(initiative_id) -> bool:
    initiatives = load_initiatives()
    key = str(initiative_id)
    if key in initiatives:
        del initiatives[key]
        save_initiatives(initiatives)
        requests = load_requests()
        new_requests = [r for r in requests if str(r.get("initiative_id")) != key]
        if len(new_requests) != len(requests):
            save_requests(new_requests)
        return True
    return False


def visible_initiatives() -> dict:
    return {k: v for k, v in load_initiatives().items() if v.get("visible")}


def has_visible_initiatives() -> bool:
    return len(visible_initiatives()) > 0


# ── Execution requests ───────────────────────────────────────────────────────

def load_requests() -> list:
    return _load_json(REQUESTS_FILE, [])


def save_requests(requests: list) -> None:
    _save_json(REQUESTS_FILE, requests)


def get_request(initiative_id, user_id):
    key, uid = str(initiative_id), str(user_id)
    for r in load_requests():
        if str(r.get("initiative_id")) == key and str(r.get("user_id")) == uid:
            return r
    return None


def has_open_request(initiative_id, user_id) -> bool:
    """True if this participant already has a pending or accepted (i.e. not
    yet resolved to rejected/completed) request for this initiative."""
    r = get_request(initiative_id, user_id)
    return bool(r) and r.get("status") in (STATUS_PENDING, STATUS_ACCEPTED)


def create_request(initiative_id, user_id, user_name: str) -> dict:
    requests = load_requests()
    entry = {
        "initiative_id": str(initiative_id),
        "user_id": str(user_id),
        "user_name": user_name or "—",
        "status": STATUS_PENDING,
        "requested_at": datetime.utcnow().isoformat(),
        "decided_at": None,
        "completed_at": None,
    }
    requests.append(entry)
    save_requests(requests)
    return entry


def set_request_status(initiative_id, user_id, status: str, **extra) -> None:
    requests = load_requests()
    key, uid = str(initiative_id), str(user_id)
    for r in requests:
        if str(r.get("initiative_id")) == key and str(r.get("user_id")) == uid:
            r["status"] = status
            r.update(extra)
            break
    save_requests(requests)


def requests_for_initiative(initiative_id, statuses=None) -> list:
    key = str(initiative_id)
    out = [r for r in load_requests() if str(r.get("initiative_id")) == key]
    if statuses:
        out = [r for r in out if r.get("status") in statuses]
    return sorted(out, key=lambda r: r.get("requested_at", ""))


def all_open_requests() -> list:
    """Every pending/accepted request across ALL initiatives, sorted
    chronologically (first-come-first-served ordering for the admin)."""
    out = [r for r in load_requests() if r.get("status") in (STATUS_PENDING, STATUS_ACCEPTED)]
    return sorted(out, key=lambda r: r.get("requested_at", ""))


def count_completed_for_user(user_id) -> int:
    uid = str(user_id)
    return sum(1 for r in load_requests() if str(r.get("user_id")) == uid and r.get("status") == STATUS_COMPLETED)


def any_completed_request_exists() -> bool:
    return any(r.get("status") == STATUS_COMPLETED for r in load_requests())


def first_completed_request():
    """The globally earliest completed request (by completed_at), or None."""
    completed = [r for r in load_requests() if r.get("status") == STATUS_COMPLETED and r.get("completed_at")]
    if not completed:
        return None
    return sorted(completed, key=lambda r: r["completed_at"])[0]
