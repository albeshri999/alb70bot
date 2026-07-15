# -*- coding: utf-8 -*-
"""
Participant-facing side of the '💡 نظام المبادرات' (Initiatives) system.

Fully independent of the word-competition flow in handlers.py — every
interaction here is a button tap, so it never touches the existing
handle_message() text-state router.
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import initiatives_storage as ins

logger = logging.getLogger(__name__)

BACK_TO_MAIN = "back_to_main"


def _back_kb(callback: str, label: str = "🔙 رجوع") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=callback)]])


async def _edit(update: Update, text: str, keyboard=None):
    kw = {"text": text, "parse_mode": ParseMode.MARKDOWN}
    if keyboard:
        kw["reply_markup"] = keyboard
    try:
        await update.callback_query.message.edit_text(**kw)
    except Exception as e:
        logger.warning("initiatives_user _edit failed: %s", e)


async def handle_menu_initiatives(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    items = ins.visible_initiatives()
    if not items:
        await _edit(update, "💡 لا توجد مبادرات متاحة حالياً.", _back_kb(BACK_TO_MAIN))
        return

    lines = ["💡 *فرص المبادرات*\n"]
    rows = []
    for iid, item in sorted(items.items(), key=lambda kv: int(kv[0])):
        lines.append(
            f"\n*{item.get('name')}*\n"
            f"{item.get('description') or ''}\n"
            f"🏆 النقاط: *{item.get('points', 0)}*"
        )
        user_id = query.from_user.id
        if ins.has_open_request(iid, user_id):
            existing = ins.get_request(iid, user_id)
            status = existing.get("status")
            label = "⏳ تم إرسال طلبك" if status == ins.STATUS_PENDING else "✅ قيد التنفيذ"
            rows.append([InlineKeyboardButton(f"{label} — {item.get('name')}", callback_data="in_noop")])
        else:
            rows.append([InlineKeyboardButton(f"✅ طلب التنفيذ — {item.get('name')}",
                                                callback_data=f"in_req_{iid}")])

    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=BACK_TO_MAIN)])
    await _edit(update, "\n".join(lines), InlineKeyboardMarkup(rows))


async def handle_initiative_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    iid = query.data.replace("in_req_", "")
    user_id = query.from_user.id

    initiative = ins.get_initiative(iid)
    if not initiative or not initiative.get("visible"):
        await query.answer("⚠️ هذه المبادرة غير متاحة.", show_alert=True)
        return

    if ins.has_open_request(iid, user_id):
        await query.answer("✅ لديك طلب سابق لهذه المبادرة.", show_alert=True)
        return

    from storage import get_user
    user = get_user(user_id) or {}
    full_name = user.get("full_name") or (query.from_user.full_name if query.from_user else "—")

    ins.create_request(iid, user_id, full_name)
    await query.answer("✅ تم إرسال طلبك للمشرف.", show_alert=True)
    await handle_menu_initiatives(update, context)


async def handle_initiative_noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
