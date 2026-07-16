# -*- coding: utf-8 -*-
"""
Independent storage layer for '🎭 إدارة المشاركات' (Submissions / talent-contest
system). Fully self-contained — does NOT read or write any file belonging to
the word-competition system, the quiz system, the team-distribution system,
or the initiatives system. Balance changes on winner-crediting go through
the existing credits.py/transactions.py helpers (called from
submissions_admin.py), exactly like every other crediting flow in the bot.

All data lives in its own JSON files under data/:
  - data/submissions.json          → contest definitions ("أفضل تلاوة قرآن"...)
  - data/submission_entries.json   → every participant's entry — a
                                      REFERENCE ONLY (channel_id + message_id),
                                      never the file itself
  - data/submissions_settings.json → the linked '📺 قناة المشاركات' channel id

Submitted media is never stored inside the bot — every entry is forwarded to
a dedicated private Telegram channel (the bot must be an admin there), and
only that channel message's reference is kept in the database. This keeps
the bot's own storage footprint tiny regardless of how many audio/video/photo
submissions come in, relying on Telegram's own file hosting.

Designed to be easy to extend with new media types later: MEDIA_TYPES is a
single dict to extend, and entries just store a generic channel reference —
no per-type branching anywhere in the storage layer itself.
"""
import json
import os
from datetime import datetime

SUBMISSIONS_FILE = "data/submissions.json"
ENTRIES_FILE      = "data/submission_entries.json"
SETTINGS_FILE     = "data/submissions_settings.json"

# ── Media types (extend this dict to support new submission formats) ───────
MEDIA_TYPES = {
    "audio": "🎤 تسجيل صوتي",
    "video": "🎥 فيديو",
    "photo": "📷 صورة",
}

ENTRY_STATUS_SUBMITTED  = "submitted"   # 🟡 بانتظار التقييم
ENTRY_STATUS_SCORED     = "scored"      # ⭐ تم التقييم — set automatically once a score is entered
ENTRY_STATUS_APPROVED   = "approved"    # 🏆 معتمدة — only after admin confirms "🏆 اعتماد"
ENTRY_STATUS_REJECTED   = "rejected"    # ❌ مرفوضة — only after admin confirms "❌ رفض"
ENTRY_STATUS_DELETED    = "deleted"     # 🗑 محذوفة — transient: shown on the channel card just
                                         # before the entry record itself is actually removed
ENTRY_STATUS_WINNER     = "winner"
ENTRY_STATUS_PARTICIPANT = "participant"
ENTRY_STATUS_PENDING_RESEND = "pending_resend"  # ⏳ channel send failed — file kept for retry

# Every entry has EXACTLY one of these at any moment — never more than one
# shown at once. Used for both the admin's "📥 المشاركات المرسلة" listing and
# the channel card text (see build_channel_caption()).
ENTRY_STATUS_LABELS = {
    ENTRY_STATUS_PENDING_RESEND: "⏳ بانتظار إعادة الإرسال",
    ENTRY_STATUS_SUBMITTED:      "🟡 بانتظار التقييم",
    ENTRY_STATUS_SCORED:         "⭐ تم التقييم",
    ENTRY_STATUS_APPROVED:       "🏆 معتمدة",
    ENTRY_STATUS_REJECTED:       "❌ مرفوضة",
    ENTRY_STATUS_DELETED:        "🗑 محذوفة",
    ENTRY_STATUS_PARTICIPANT:    "🔹 مشارك",
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


# ── Settings (📺 قناة المشاركات) ─────────────────────────────────────────────
#
# Linking is done entirely via Telegram's native Request Chat API
# (KeyboardButtonRequestChat — see submissions_admin.sb_channel_request /
# sb_channel_shared) — never by forwarding a message, a /link command, or
# typing a chat id by hand.

def load_settings() -> dict:
    return _load_json(SETTINGS_FILE, {})


def get_channel_id():
    """The linked channel's chat id, or None if not configured yet."""
    return load_settings().get("channel_id")


def get_channel_title():
    return load_settings().get("channel_title")


def get_channel_linked_at():
    return load_settings().get("channel_linked_at")


def is_channel_configured() -> bool:
    return get_channel_id() is not None


def link_channel(channel_id, title: str) -> None:
    settings = load_settings()
    settings["channel_id"] = channel_id
    settings["channel_title"] = title or "—"
    settings["channel_linked_at"] = datetime.utcnow().isoformat()
    _save_json(SETTINGS_FILE, settings)


def unlink_channel() -> None:
    settings = load_settings()
    settings.pop("channel_id", None)
    settings.pop("channel_title", None)
    settings.pop("channel_linked_at", None)
    _save_json(SETTINGS_FILE, settings)


def channel_message_link(channel_id, message_id) -> str:
    """Deep link to a specific message inside the channel
    (https://t.me/c/<internal_id>/<message_id>) — works for anyone who is
    already a member of that channel, exactly like tapping the message."""
    cid = str(channel_id)
    if cid.startswith("-100"):
        cid = cid[4:]
    elif cid.startswith("-"):
        cid = cid[1:]
    return f"https://t.me/c/{cid}/{message_id}"


# ── Submissions (contest definitions) ───────────────────────────────────────

def load_submissions() -> dict:
    return _load_json(SUBMISSIONS_FILE, {})


def save_submissions(data: dict) -> None:
    _save_json(SUBMISSIONS_FILE, data)


def get_submission(submission_id) -> dict:
    return load_submissions().get(str(submission_id), {})


def save_submission(submission_id, data: dict) -> None:
    submissions = load_submissions()
    submissions[str(submission_id)] = data
    save_submissions(submissions)


def next_submission_id() -> str:
    submissions = load_submissions()
    nums = [int(k) for k in submissions.keys() if str(k).isdigit()]
    return str((max(nums) + 1) if nums else 1)


def create_submission(name: str, description: str, media_type: str, points: int,
                       num_winners: int, max_score: int, deadline_iso: str,
                       allow_edit: bool, hide_names: bool, visible: bool) -> str:
    submission_id = next_submission_id()
    save_submission(submission_id, {
        "id": submission_id,
        "name": name,
        "description": description or "",
        "media_type": media_type,
        "points": int(points),
        "num_winners": int(num_winners),
        "max_score": int(max_score),
        "deadline": deadline_iso,
        "allow_edit": bool(allow_edit),
        # Judging anonymity — see display_identity() below. Judges only ever
        # see a stable 'P-001'-style id until results are finalized.
        "hide_names": bool(hide_names),
        "visible": bool(visible),
        "results_finalized": False,
        "created_at": datetime.utcnow().isoformat(),
    })
    return submission_id


def update_submission_field(submission_id, **fields) -> None:
    submission = get_submission(submission_id)
    if submission:
        submission.update(fields)
        save_submission(submission_id, submission)


def set_submission_visible(submission_id, visible: bool) -> None:
    update_submission_field(submission_id, visible=bool(visible))


def delete_submission(submission_id) -> bool:
    submissions = load_submissions()
    key = str(submission_id)
    if key in submissions:
        del submissions[key]
        save_submissions(submissions)
        entries = load_entries()
        new_entries = [e for e in entries if str(e.get("submission_id")) != key]
        if len(new_entries) != len(entries):
            save_entries(new_entries)
        return True
    return False


def visible_submissions() -> dict:
    return {k: v for k, v in load_submissions().items() if v.get("visible")}


def has_visible_submissions() -> bool:
    return len(visible_submissions()) > 0


def is_deadline_passed(submission: dict) -> bool:
    deadline = submission.get("deadline")
    if not deadline:
        return False
    try:
        return datetime.utcnow() >= datetime.fromisoformat(deadline)
    except Exception:
        return False


def is_open_for_submissions(submission: dict) -> bool:
    """Whether a participant may still submit/replace an entry right now."""
    return submission.get("visible", False) and not is_deadline_passed(submission)


def can_edit_entry(submission: dict) -> bool:
    return bool(submission.get("allow_edit")) and not is_deadline_passed(submission)


def parse_deadline_text(text: str):
    """Parses 'YYYY-MM-DD HH:MM' → ISO string, or None if invalid."""
    text = text.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    return None


def format_deadline(iso_str) -> str:
    if not iso_str:
        return "—"
    try:
        return datetime.fromisoformat(iso_str).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"


# ── Entries (participants' uploaded submissions) ────────────────────────────

def load_entries() -> list:
    return _load_json(ENTRIES_FILE, [])


def save_entries(entries: list) -> None:
    _save_json(ENTRIES_FILE, entries)


def get_entry(submission_id, user_id):
    key, uid = str(submission_id), str(user_id)
    for e in load_entries():
        if str(e.get("submission_id")) == key and str(e.get("user_id")) == uid:
            return e
    return None


def has_entry(submission_id, user_id) -> bool:
    return get_entry(submission_id, user_id) is not None


def next_judge_id(submission_id) -> str:
    """Next sequential anonymous judging id ('P-001', 'P-002', ...) for this
    submission — stable per participant (see create_or_replace_entry, which
    reuses an existing judge_id across replacements instead of reassigning)."""
    existing = [e for e in entries_for_submission(submission_id) if e.get("judge_id")]
    return f"P-{len(existing) + 1:03d}"


def display_identity(entry: dict, submission: dict) -> str:
    """What a judge/admin should see for this entry: the real name, unless
    the contest hides names AND results aren't finalized yet — in which
    case only the anonymous judging id is shown. After finalize_submission()
    runs, the real identity is revealed (per the requirement)."""
    if submission.get("hide_names") and not submission.get("results_finalized"):
        return f"🎖 المتسابق رقم: {entry.get('judge_id', '—')}"
    return entry.get("user_name", "—")


def create_or_replace_entry(submission_id, user_id, user_name: str, submission_name: str,
                             channel_id, message_id, media_type: str,
                             pending_file_id: str = None) -> dict:
    """Adds a new entry — normally storing only a REFERENCE to the file
    already forwarded into the submissions channel (channel_id + message_id),
    never the file itself — replacing (deleting) any previous entry by the
    same user for the same submission ('يحذف المشاركة السابقة ويحتفظ بآخر
    واحدة فقط'). Preserves the same anonymous judge_id across a replacement
    so a participant's identity stays consistently hidden/labeled throughout.

    If the channel forward failed, pass channel_id=None, message_id=None,
    and pending_file_id=<the raw file id> instead — the ONLY case where a
    file reference is kept in the database, and only temporarily until
    resend_pending_entries() succeeds (see mark_entry_sent())."""
    entries = load_entries()
    key, uid = str(submission_id), str(user_id)
    existing = next((e for e in entries if str(e.get("submission_id")) == key
                      and str(e.get("user_id")) == uid), None)
    judge_id = existing.get("judge_id") if existing else next_judge_id(submission_id)

    entries = [e for e in entries if not (str(e.get("submission_id")) == key and str(e.get("user_id")) == uid)]
    entry = {
        "submission_id": key,
        "submission_name": submission_name,
        "user_id": uid,
        "user_name": user_name or "—",
        "judge_id": judge_id,
        "channel_id": channel_id,
        "message_id": message_id,
        "pending_file_id": pending_file_id,
        "file_type": media_type,
        "submitted_at": datetime.utcnow().isoformat(),
        "score": None,
        "rank": None,
        "status": ENTRY_STATUS_PENDING_RESEND if pending_file_id else ENTRY_STATUS_SUBMITTED,
    }
    entries.append(entry)
    save_entries(entries)
    return entry


def mark_entry_sent(submission_id, user_id, channel_id, message_id) -> bool:
    """Upgrades a pending ('⏳ بانتظار إعادة الإرسال') entry to a normal sent
    one once resend_pending_entries() succeeds — clears the temporarily-kept
    file id, since the channel message reference is now the source of truth."""
    entries = load_entries()
    key, uid = str(submission_id), str(user_id)
    changed = False
    for e in entries:
        if str(e.get("submission_id")) == key and str(e.get("user_id")) == uid:
            e["channel_id"] = channel_id
            e["message_id"] = message_id
            e["pending_file_id"] = None
            e["status"] = ENTRY_STATUS_SUBMITTED
            changed = True
            break
    if changed:
        save_entries(entries)
    return changed


def pending_resend_entries() -> list:
    """Every entry whose channel forward previously failed and is still
    waiting to be retried — see '🔄 إعادة إرسال المشاركات المعلقة'."""
    out = [e for e in load_entries() if e.get("status") == ENTRY_STATUS_PENDING_RESEND]
    return sorted(out, key=lambda e: e.get("submitted_at", ""))


def count_entries_for_channel(channel_id) -> int:
    """How many entries currently reference the given channel — used for
    '📤 عدد المشاركات المرسلة' on the channel management page."""
    cid = str(channel_id)
    return sum(1 for e in load_entries() if str(e.get("channel_id")) == cid)


async def check_channel_status(bot):
    """Verifies the linked channel still exists, the bot is still an admin
    there, and can still post messages — called before EVERY forward to the
    channel, and also shown live on the channel management page. Returns
    (ok: bool, reason: str) — reason is empty when ok is True."""
    channel_id = get_channel_id()
    if not channel_id:
        return False, "لا توجد قناة مرتبطة بنظام المشاركات."
    try:
        await bot.get_chat(channel_id)
    except Exception:
        return False, "القناة المرتبطة لم تعد موجودة أو تعذّر الوصول إليها."
    try:
        member = await bot.get_chat_member(channel_id, bot.id)
    except Exception:
        return False, "تعذّر التحقق من صلاحيات البوت داخل قناة المشاركات."
    status = getattr(member, "status", None)
    if status not in ("administrator", "creator"):
        return False, "البوت لم يعد مشرفاً داخل قناة المشاركات."
    can_post = status == "creator" or getattr(member, "can_post_messages", False)
    if not can_post:
        return False, "البوت لا يملك صلاحية إرسال الرسائل داخل قناة المشاركات."
    return True, ""


def delete_entry(submission_id, user_id) -> bool:
    entries = load_entries()
    key, uid = str(submission_id), str(user_id)
    kept = [e for e in entries if not (str(e.get("submission_id")) == key and str(e.get("user_id")) == uid)]
    removed = len(kept) != len(entries)
    if removed:
        save_entries(kept)
    return removed


def entries_for_submission(submission_id) -> list:
    key = str(submission_id)
    out = [e for e in load_entries() if str(e.get("submission_id")) == key]
    return sorted(out, key=lambda e: e.get("submitted_at", ""))


def all_entries() -> list:
    return load_entries()


def set_entry_score(submission_id, user_id, score) -> None:
    """Saving a score ALWAYS moves the entry to exactly '⭐ تم التقييم',
    overriding whatever status it had before — scoring never also implies
    approval; that only ever happens via approve_entry() after explicit
    admin confirmation."""
    entries = load_entries()
    key, uid = str(submission_id), str(user_id)
    for e in entries:
        if str(e.get("submission_id")) == key and str(e.get("user_id")) == uid:
            e["score"] = score
            e["status"] = ENTRY_STATUS_SCORED
            break
    save_entries(entries)


def set_entry_status(submission_id, user_id, status: str) -> None:
    """Low-level setter — prefer approve_entry()/reject_entry() below for
    the confirm-gated moderation actions."""
    entries = load_entries()
    key, uid = str(submission_id), str(user_id)
    for e in entries:
        if str(e.get("submission_id")) == key and str(e.get("user_id")) == uid:
            e["status"] = status
            break
    save_entries(entries)


def approve_entry(submission_id, user_id) -> None:
    """🏆 معتمدة — only ever called after the admin explicitly confirms
    '✅ نعم' on the '🏆 اعتماد' confirmation prompt."""
    set_entry_status(submission_id, user_id, ENTRY_STATUS_APPROVED)


def reject_entry(submission_id, user_id) -> None:
    """❌ مرفوضة — only ever called after the admin explicitly confirms
    '✅ نعم' on the '❌ رفض' confirmation prompt."""
    set_entry_status(submission_id, user_id, ENTRY_STATUS_REJECTED)


def build_channel_caption(submission: dict, entry: dict) -> str:
    """The SINGLE canonical caption for a submission's card inside the
    channel — used when first sending it, when resending a pending entry,
    and every time its status changes (scored/approved/rejected/deleted),
    so the card is always edited in place rather than re-composed ad hoc in
    multiple places."""
    hide_names = submission.get("hide_names")
    if hide_names:
        identity_line = f"🎖 المتسابق رقم: {entry.get('judge_id', '—')}"
    else:
        identity_line = f"👤 اسم المتسابق: {entry.get('user_name', '—')}\n🆔 معرف المتسابق: {entry.get('user_id', '—')}"

    status_label = ENTRY_STATUS_LABELS.get(entry.get("status"), "—")
    score = entry.get("score")
    score_line = f"💯 الدرجة: {score}" if score is not None else "💯 الدرجة: —"

    return (
        f"🏆 {submission.get('name')}\n\n"
        f"{identity_line}\n"
        f"📅 {format_deadline(entry.get('submitted_at'))}\n"
        f"🏷 نوع المشاركة: {MEDIA_TYPES.get(entry.get('file_type'), '—')}\n\n"
        f"📍 الحالة: {status_label}\n"
        f"{score_line}"
    )


def finalize_submission(submission_id):
    """Ranks all entries by score (highest first, unscored entries last),
    marks the top `num_winners` as winners (rank 1..N) and the rest as
    plain participants, and locks the submission's results. Returns
    (winners, others) — lists of entry dicts (winners already carry their
    'rank'). Does NOT credit points or send messages — that's the caller's
    job (submissions_admin.py), matching how initiatives/quiz crediting
    already works outside the storage layer."""
    submission = get_submission(submission_id)
    if not submission:
        return [], []
    num_winners = int(submission.get("num_winners", 0))

    entries = entries_for_submission(submission_id)
    eligible = [e for e in entries if e.get("status") != ENTRY_STATUS_REJECTED]
    scored = [e for e in eligible if e.get("score") is not None]
    unscored = [e for e in eligible if e.get("score") is None]
    scored.sort(key=lambda e: e["score"], reverse=True)
    ordered = scored + unscored

    winners, others = [], []
    all_entries_data = load_entries()
    key = str(submission_id)
    for i, e in enumerate(ordered):
        is_winner = i < num_winners and e.get("score") is not None
        rank = i + 1 if is_winner else None
        status = ENTRY_STATUS_WINNER if is_winner else ENTRY_STATUS_PARTICIPANT
        for stored in all_entries_data:
            if str(stored.get("submission_id")) == key and str(stored.get("user_id")) == e.get("user_id"):
                stored["rank"] = rank
                stored["status"] = status
                break
        e["rank"], e["status"] = rank, status
        (winners if is_winner else others).append(e)

    save_entries(all_entries_data)
    update_submission_field(submission_id, results_finalized=True)
    return winners, others
