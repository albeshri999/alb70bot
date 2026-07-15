# -*- coding: utf-8 -*-
"""
Participant-facing side of the '🎭 نظام المشاركات' (Submissions / talent
contest) system.

Fully independent of the word-competition flow in handlers.py. Browsing and
picking a submission is button-driven (like the other systems), but the
actual upload step necessarily waits for a MEDIA message (voice/audio,
video, or photo) — handled by handle_submission_media(), a single global
MessageHandler registered in main.py that no-ops instantly unless this
module just told it (via context.user_data) that this specific user is
mid-upload for a specific submission. It never interferes with the
word-competition's text-answer handler or any other message handling.
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import submissions_storage as subs

logger = logging.getLogger(__name__)

BACK_TO_MAIN = "back_to_main"
PENDING_KEY = "sb_pending_upload"  # holds the submission_id awaiting a file


def _back_kb(callback: str, label: str = "🔙 رجوع") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=callback)]])


async def _edit(update: Update, text: str, keyboard=None):
    kw = {"text": text, "parse_mode": ParseMode.MARKDOWN}
    if keyboard:
        kw["reply_markup"] = keyboard
    try:
        await update.callback_query.message.edit_text(**kw)
    except Exception as e:
        logger.warning("submissions_user _edit failed: %s", e)


def _submission_icon(submission: dict) -> str:
    """Best-effort thematic icon for display, falling back to the plain
    media-type icon — same keyword idea as the achievement theme badges,
    kept local here to avoid a hard dependency on achievements_storage."""
    name = submission.get("name", "")
    keyword_icons = [
        (("قرآن", "تلاوة"), "📖"),
        (("أذان",), "🕌"),
        (("نشيد", "أناشيد"), "🎤"),
        (("تعبير", "متحدث", "حديث"), "🎙"),
        (("تصوير", "صورة"), "📷"),
    ]
    for keywords, icon in keyword_icons:
        if any(k in name for k in keywords):
            return icon
    return {"audio": "🎤", "video": "🎥", "photo": "📷"}.get(submission.get("media_type"), "🎭")


async def handle_menu_submissions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    items = subs.visible_submissions()
    if not items:
        await _edit(update, "🎭 لا توجد مشاركات متاحة حالياً.", _back_kb(BACK_TO_MAIN))
        return

    rows = [
        [InlineKeyboardButton(f"{_submission_icon(item)} {item.get('name')}", callback_data=f"sb_view_{k}")]
        for k, item in sorted(items.items(), key=lambda kv: int(kv[0]))
    ]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=BACK_TO_MAIN)])
    await _edit(update, "🎭 *المشاركات*\n\nاختر مشاركة:", InlineKeyboardMarkup(rows))


async def handle_submission_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    submission_id = query.data.replace("sb_view_", "")
    user_id = query.from_user.id

    submission = subs.get_submission(submission_id)
    if not submission or not submission.get("visible"):
        await _edit(update, "⚠️ هذه المشاركة غير متاحة.", _back_kb("menu_submissions"))
        return

    entry = subs.get_entry(submission_id, user_id)
    editable_line = "✅ يسمح بالتعديل" if submission.get("allow_edit") else "❌ لا يسمح بالتعديل"
    deadline_passed = subs.is_deadline_passed(submission)

    text = (
        f"{_submission_icon(submission)} *{submission.get('name')}*\n\n"
        f"{submission.get('description') or '—'}\n\n"
        f"📎 نوع المشاركة: *{subs.MEDIA_TYPES.get(submission.get('media_type'), '—')}*\n"
        f"🔢 عدد الفائزين: *{submission.get('num_winners', 0)}*\n"
        f"🏆 النقاط: *{submission.get('points', 0)}*\n"
        f"⏰ آخر موعد: {subs.format_deadline(submission.get('deadline'))}\n"
        f"✏️ {editable_line}"
    )

    rows = []
    if deadline_passed:
        text += "\n\n⚠️ انتهى موعد استقبال هذه المشاركة."
    elif entry and not submission.get("allow_edit"):
        text += "\n\n✅ لقد أرسلت مشاركتك مسبقاً."
    elif entry and submission.get("allow_edit"):
        text += "\n\n✅ لقد أرسلت مشاركتك، ويمكنك استبدالها قبل انتهاء الموعد."
        rows.append([InlineKeyboardButton("🔁 استبدال المشاركة", callback_data=f"sb_submit_{submission_id}")])
    else:
        rows.append([InlineKeyboardButton("📤 إرسال المشاركة", callback_data=f"sb_submit_{submission_id}")])

    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="menu_submissions")])
    await _edit(update, text, InlineKeyboardMarkup(rows))


async def handle_submission_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    submission_id = query.data.replace("sb_submit_", "")
    user_id = query.from_user.id

    submission = subs.get_submission(submission_id)
    if not submission or not submission.get("visible"):
        await query.answer("⚠️ هذه المشاركة غير متاحة.", show_alert=True)
        return
    if subs.is_deadline_passed(submission):
        await query.answer("⚠️ انتهى موعد استقبال هذه المشاركة.", show_alert=True)
        return
    entry = subs.get_entry(submission_id, user_id)
    if entry and not submission.get("allow_edit"):
        await query.answer("⚠️ لقد أرسلت مشاركتك مسبقاً ولا يمكن تعديلها.", show_alert=True)
        return

    await query.answer()
    context.user_data[PENDING_KEY] = submission_id
    prompt = {
        "audio": "🎤 أرسل الآن التسجيل الصوتي الخاص بمشاركتك.",
        "video": "🎥 أرسل الآن الفيديو الخاص بمشاركتك.",
        "photo": "📷 أرسل الآن الصورة الخاصة بمشاركتك.",
    }.get(submission.get("media_type"), "أرسل الآن ملف مشاركتك.")
    await _edit(update, prompt, _back_kb(f"sb_view_{submission_id}"))


async def handle_submission_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global media handler — a strict no-op unless this exact user is
    currently expected to upload for a specific submission (never touches
    any other message flow in the bot)."""
    submission_id = context.user_data.get(PENDING_KEY)
    if not submission_id:
        return

    message = update.message
    user_id = update.effective_user.id
    submission = subs.get_submission(submission_id)
    if not submission:
        context.user_data.pop(PENDING_KEY, None)
        return

    media_type = submission.get("media_type")
    file_id = None
    if media_type == "audio" and (message.voice or message.audio):
        file_id = (message.voice or message.audio).file_id
    elif media_type == "video" and message.video:
        file_id = message.video.file_id
    elif media_type == "photo" and message.photo:
        file_id = message.photo[-1].file_id

    if not file_id:
        expected = subs.MEDIA_TYPES.get(media_type, "—")
        await message.reply_text(f"❌ نوع الملف غير مقبول لهذه المشاركة.\n\nالمطلوب: {expected}")
        return

    if subs.is_deadline_passed(submission):
        context.user_data.pop(PENDING_KEY, None)
        await message.reply_text("⚠️ انتهى موعد استقبال هذه المشاركة.")
        return
    entry = subs.get_entry(submission_id, user_id)
    if entry and not submission.get("allow_edit"):
        context.user_data.pop(PENDING_KEY, None)
        await message.reply_text("⚠️ لقد أرسلت مشاركتك مسبقاً ولا يمكن تعديلها.")
        return

    from storage import get_user
    user = get_user(user_id) or {}
    full_name = user.get("full_name") or (update.effective_user.full_name if update.effective_user else "—")

    subs.create_or_replace_entry(submission_id, user_id, full_name, file_id, media_type)
    context.user_data.pop(PENDING_KEY, None)
    await message.reply_text("✅ تم استلام مشاركتك بنجاح. بالتوفيق!")


async def handle_menu_my_submissions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Optional convenience view — participant sees every submission
    they've personally uploaded an entry for, with its current status."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    all_entries = subs.all_entries()
    mine = [e for e in all_entries if str(e.get("user_id")) == str(user_id)]
    if not mine:
        await _edit(update, "🎭 لم تشارك في أي مشاركة بعد.", _back_kb(BACK_TO_MAIN))
        return

    lines = ["🎭 *مشاركاتي*"]
    for e in sorted(mine, key=lambda x: x.get("submitted_at", ""), reverse=True):
        submission = subs.get_submission(e.get("submission_id"))
        name = submission.get("name", "—") if submission else "—"
        if e.get("status") == subs.ENTRY_STATUS_WINNER:
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(e.get("rank"), "🏆")
            status = f"{medal} فائز (المركز {e.get('rank')})"
        elif e.get("status") == subs.ENTRY_STATUS_PARTICIPANT:
            status = "🔹 مشارك"
        elif e.get("score") is not None:
            status = f"📝 تم التقييم ({e.get('score')})"
        else:
            status = "📥 مرسلة (بانتظار التقييم)"
        lines.append(f"\n{name}\n{status}\n---------------------")

    await _edit(update, "\n".join(lines), _back_kb(BACK_TO_MAIN))
