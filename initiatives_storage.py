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
STATUS_CANCELLED = "cancelled"
STATUS_EXCLUDED  = "excluded"

# Statuses that count as "this participant currently has an open initiative"
# — blocks them from requesting any other initiative until resolved.
OPEN_STATUSES = (STATUS_PENDING, STATUS_ACCEPTED)

STATUS_LABELS = {
    STATUS_PENDING:   "🟡 قيد الانتظار",
    STATUS_ACCEPTED:  "🟢 قيد التنفيذ",
    STATUS_COMPLETED: "✔ مكتملة",
    STATUS_REJECTED:  "❌ مرفوضة",
    STATUS_CANCELLED: "🚫 ملغاة",
    STATUS_EXCLUDED:  "🚫 مستبعد",
}

# ── Initiative-level status (📍 حالة المبادرة) ───────────────────────────────
INIT_STATUS_OPEN        = "open"         # 🟢 مفتوحة — accepting requests
INIT_STATUS_IN_PROGRESS = "in_progress"  # 🟡 قيد التنفيذ — executors chosen, locked
INIT_STATUS_COMPLETED   = "completed"    # ✔ مكتملة — every executor's work approved
INIT_STATUS_CLOSED      = "closed"       # ⛔ مغلقة — manually closed by an admin

INIT_STATUS_LABELS = {
    INIT_STATUS_OPEN:        "🟢 مفتوحة",
    INIT_STATUS_IN_PROGRESS: "🟡 قيد التنفيذ",
    INIT_STATUS_COMPLETED:   "✔ مكتملة",
    INIT_STATUS_CLOSED:      "⛔ مغلقة",
}


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


def create_initiative(name: str, description: str, points: int, visible: bool,
                       max_participants: int) -> str:
    initiative_id = next_initiative_id()
    save_initiative(initiative_id, {
        "id": initiative_id,
        "name": name,
        "description": description or "",
        "points": int(points),
        "visible": bool(visible),
        # Maximum number of EXECUTORS (accepted+completed participants) this
        # initiative can hold. None means unlimited — this is also the
        # default for initiatives created before this feature existed, so
        # they keep accepting requests unchanged (never auto-locking).
        "max_participants": int(max_participants) if max_participants else None,
        # Explicit status machine (📍 حالة المبادرة) — see INIT_STATUS_*
        # constants above. Requests are only accepted while this is "open".
        "status": INIT_STATUS_OPEN,
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


def get_max_participants(initiative: dict):
    """None means unlimited (covers initiatives created before this field existed)."""
    return initiative.get("max_participants")


def get_initiative_status(initiative: dict) -> str:
    """Defaults to 'open' — covers initiatives created before this field
    existed, so they keep accepting requests exactly as before."""
    return initiative.get("status") or INIT_STATUS_OPEN


def set_initiative_status(initiative_id, status: str) -> None:
    update_initiative_field(initiative_id, status=status)


def is_open_for_requests(initiative: dict) -> bool:
    """The ONLY gate on new execution requests — whether the initiative's
    own status is 'open'. Capacity is never checked here: an open
    initiative accepts unlimited requests regardless of max_participants;
    the admin picks executors manually afterwards (see accept_participant())."""
    return get_initiative_status(initiative) == INIT_STATUS_OPEN


def accepted_count(initiative_id) -> int:
    """How many participants currently hold an executor slot on this
    initiative (accepted-and-working, or already completed). Rejected,
    cancelled, and excluded requests never occupy (or no longer occupy) a
    slot — this is the count compared against max_participants."""
    key = str(initiative_id)
    return sum(
        1 for r in load_requests()
        if str(r.get("initiative_id")) == key and r.get("status") in (STATUS_ACCEPTED, STATUS_COMPLETED)
    )


def active_executor_count(initiative_id) -> int:
    """How many executors are currently ACCEPTED but not yet marked
    completed — used to detect 'every executor finished' (→ ✔ مكتملة)."""
    key = str(initiative_id)
    return sum(1 for r in load_requests()
               if str(r.get("initiative_id")) == key and r.get("status") == STATUS_ACCEPTED)


def is_full(initiative: dict) -> bool:
    """True once accepted_count reaches max_participants. Used only to
    decide when to auto-lock an 'open' initiative into 'in_progress' —
    never to block requests (see is_open_for_requests())."""
    max_p = get_max_participants(initiative)
    if not max_p:
        return False
    return accepted_count(initiative.get("id")) >= int(max_p)


def remaining_seats(initiative: dict):
    """None if unlimited, otherwise how many executor-slots are still free
    (never negative)."""
    max_p = get_max_participants(initiative)
    if not max_p:
        return None
    return max(0, int(max_p) - accepted_count(initiative.get("id")))


def initiative_status_label(initiative: dict) -> str:
    return INIT_STATUS_LABELS.get(get_initiative_status(initiative), INIT_STATUS_LABELS[INIT_STATUS_OPEN])


def accept_participant(initiative_id, user_id) -> list:
    """Accept one pending participant as an executor. If this fills the
    last available seat, automatically locks the initiative ('in_progress')
    and mass-rejects every other still-pending request for it. Returns the
    list of request records that were just auto-rejected (each still has
    its 'user_id') so the caller can notify them — empty list if the
    initiative didn't just become full."""
    set_request_status(initiative_id, user_id, STATUS_ACCEPTED,
                        decided_at=datetime.utcnow().isoformat())

    initiative = get_initiative(initiative_id)
    if not initiative:
        return []
    max_p = get_max_participants(initiative)
    if not max_p or accepted_count(initiative_id) < int(max_p):
        return []  # still open — either unlimited or seats remain

    # Last seat just filled — lock the initiative and mass-reject the rest.
    set_initiative_status(initiative_id, INIT_STATUS_IN_PROGRESS)
    return reject_all_pending(initiative_id)


def reject_all_pending(initiative_id) -> list:
    """Reject every still-PENDING request for this initiative (used both
    when the initiative auto-locks and is available for manual bulk use).
    Returns the list of request records that were rejected."""
    requests = load_requests()
    key = str(initiative_id)
    rejected = []
    now = datetime.utcnow().isoformat()
    for r in requests:
        if str(r.get("initiative_id")) == key and r.get("status") == STATUS_PENDING:
            r["status"] = STATUS_REJECTED
            r["decided_at"] = now
            rejected.append(r)
    if rejected:
        save_requests(requests)
    return rejected


def complete_participant(initiative_id, user_id, points_awarded: int) -> bool:
    """Mark one executor's work as approved/completed. Returns True if this
    was the LAST remaining active executor — i.e. the initiative should now
    be marked ✔ مكتملة (every chosen executor's work has been approved)."""
    set_request_status(initiative_id, user_id, STATUS_COMPLETED,
                        completed_at=datetime.utcnow().isoformat(),
                        points_awarded=points_awarded)
    if active_executor_count(initiative_id) == 0:
        initiative = get_initiative(initiative_id)
        if initiative and get_initiative_status(initiative) == INIT_STATUS_IN_PROGRESS:
            set_initiative_status(initiative_id, INIT_STATUS_COMPLETED)
            return True
    return False


def _remove_executor(initiative_id, user_id, new_status: str, **extra) -> bool:
    """Shared logic for any action that removes an ACCEPTED executor
    (excluding them, or cancelling their acceptance) — sets their request to
    `new_status` and, if that frees a seat below the cap on a locked
    ('in_progress') initiative, automatically reopens it. Returns True if
    the initiative was just reopened."""
    set_request_status(initiative_id, user_id, new_status, **extra)

    initiative = get_initiative(initiative_id)
    if not initiative:
        return False
    max_p = get_max_participants(initiative)
    if max_p and accepted_count(initiative_id) < int(max_p) \
            and get_initiative_status(initiative) == INIT_STATUS_IN_PROGRESS:
        set_initiative_status(initiative_id, INIT_STATUS_OPEN)
        return True
    return False


def exclude_participant(initiative_id, user_id) -> bool:
    """Remove one executor from the initiative without crediting any
    points. If this frees a seat below the cap, automatically reopens the
    initiative to new requests. Returns True if it was reopened."""
    return _remove_executor(initiative_id, user_id, STATUS_EXCLUDED,
                             excluded_at=datetime.utcnow().isoformat())


def executors_for_initiative(initiative_id) -> list:
    """Every participant currently ACCEPTED (chosen executor, not yet
    marked completed) for this initiative — used for '👥 المنفذون'."""
    return requests_for_initiative(initiative_id, statuses=(STATUS_ACCEPTED,))


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
    yet resolved) request for this SPECIFIC initiative."""
    r = get_request(initiative_id, user_id)
    return bool(r) and r.get("status") in OPEN_STATUSES


def user_open_request(user_id):
    """This participant's single open (pending/accepted) request, across
    ALL initiatives, or None. A participant may only ever have one at a
    time — see user_has_any_open_request()."""
    uid = str(user_id)
    for r in load_requests():
        if str(r.get("user_id")) == uid and r.get("status") in OPEN_STATUSES:
            return r
    return None


def user_has_any_open_request(user_id) -> bool:
    return user_open_request(user_id) is not None


def requests_for_user(user_id) -> list:
    """Every request (any status) this participant has ever made, most
    recent first — used for '📌 مبادراتي'."""
    uid = str(user_id)
    out = [r for r in load_requests() if str(r.get("user_id")) == uid]
    return sorted(out, key=lambda r: r.get("requested_at", ""), reverse=True)


def requests_by_status(status) -> list:
    """Every request across ALL initiatives with the given status (or all
    statuses if status is falsy), chronologically — used for the admin's
    '📊 طلبات التنفيذ' filter view."""
    out = load_requests() if not status else [r for r in load_requests() if r.get("status") == status]
    return sorted(out, key=lambda r: r.get("requested_at", ""))


def cancel_request(initiative_id, user_id, **extra) -> bool:
    """Cancel an accepted executor's participation (via the admin's
    '🚫 إلغاء المبادرة' action). Same seat-freeing/reopen behavior as
    exclude_participant() — the only difference is the resulting status
    label ('ملغاة' vs 'مستبعد'). Returns True if this reopened the
    initiative to new requests."""
    return _remove_executor(initiative_id, user_id, STATUS_CANCELLED,
                             cancelled_at=datetime.utcnow().isoformat(), **extra)


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


def delete_request(initiative_id, user_id) -> bool:
    """Remove ONE request record entirely (used by 'حذف النتيجة' / 'حذف طلب'
    in the results-management screens). Does not touch balance — the caller
    is responsible for reversing any credited points first."""
    requests = load_requests()
    key, uid = str(initiative_id), str(user_id)
    kept = [r for r in requests if not (str(r.get("initiative_id")) == key and str(r.get("user_id")) == uid)]
    removed = len(kept) != len(requests)
    if removed:
        save_requests(kept)
    return removed


def delete_all_requests_by_status(initiative_id, status) -> list:
    """Remove every request for this initiative with the given status.
    Returns the list of removed request records (so the caller can reverse
    any credited points for each one, e.g. for bulk-deleting completions)."""
    requests = load_requests()
    key = str(initiative_id)
    removed = [r for r in requests if str(r.get("initiative_id")) == key and r.get("status") == status]
    if removed:
        kept = [r for r in requests if not (str(r.get("initiative_id")) == key and r.get("status") == status)]
        save_requests(kept)
    return removed


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
