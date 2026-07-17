# -*- coding: utf-8 -*-
"""
Participant-facing side of the '🎤 إلقاء الكلمات' (Word-Delivery) system.

Fully independent of the word-competition flow in handlers.py — every
interaction here is a button tap, so it never touches the existing
handle_message() text-state router.
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import words_storage as ws
from storage import get_user, load_days

logger = logging.getLogger(__name__)

BACK_TO_MAIN = "back_to_main"


def _back_kb(callback: str = BACK_TO_MAIN) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=callback)]])


async def _edit(update: Update, text: str, keyboard=None):
    kw = {"text": text, "parse_mode": ParseMode.MARKDOWN}
    if keyboard:
        kw["reply_markup"] = keyboard
    try:
        await update.callback_query.message.edit_text(**kw)
    except Exception as e:
        logger.warning("words_user _edit failed: %s", e)


def has_open_words_announcement() -> bool:
    """Used by handlers.py's main-menu builder to decide whether to show
    the '🎤 إلقاء الكلمات' button at all."""
    return ws.any_announcement_open()


async def handle_menu_words(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    open_days = ws.open_day_keys()
    if not open_days:
        await _edit(update, "🎤 لا يوجد إعلان مفتوح لإلقاء الكلمات حالياً.", _back_kb())
        return

    if ws.has_delivered(user_id):
        await _edit(update, "نعتذر، لا يمكن تكرار المشاركة في إلقاء الكلمات.", _back_kb())
        return

    if ws.user_has_open_volunteer_entry(user_id):
        await _edit(update, "✅ أنت مسجل بالفعل ضمن الراغبين، بانتظار اختيارك من قبل المشرف.", _back_kb())
        return

    if len(open_days) == 1:
        await _register(update, context, open_days[0])
        return

    days = load_days()
    rows = [
        [InlineKeyboardButton(days.get(k, {}).get("name", k), callback_data=f"wduser_day_{k}")]
        for k in open_days
    ]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=BACK_TO_MAIN)])
    await _edit(update, "🎤 اختر اليوم الذي تريد إلقاء كلمة فيه:", InlineKeyboardMarkup(rows))


async def handle_words_day_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    day_key = query.data.replace("wduser_day_", "")
    await _register(update, context, day_key)


async def _register(update: Update, context: ContextTypes.DEFAULT_TYPE, day_key: str) -> None:
    query = update.callback_query
    user_id = query.from_user.id

    if not ws.is_announcement_open(day_key):
        await _edit(update, "⚠️ هذا الإعلان لم يعد مفتوحاً.", _back_kb())
        return
    if ws.has_delivered(user_id):
        await _edit(update, "نعتذر، لا يمكن تكرار المشاركة في إلقاء الكلمات.", _back_kb())
        return
    if ws.get_volunteer(day_key, user_id):
        await _edit(update, "✅ أنت مسجل بالفعل ضمن الراغبين، بانتظار اختيارك من قبل المشرف.", _back_kb())
        return

    user = get_user(user_id) or {}
    full_name = user.get("full_name") or (query.from_user.full_name if query.from_user else "—")
    ws.add_volunteer(day_key, user_id, full_name)
    await _edit(update, "✅ تم تسجيلك ضمن الراغبين في إلقاء الكلمات. بانتظار اختيارك من قبل المشرف.", _back_kb())
