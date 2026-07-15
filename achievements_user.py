# -*- coding: utf-8 -*-
"""
Participant-facing side of the '🏅 نظام الإنجازات' (Achievements) system.

Fully independent — every interaction here is a button tap, so it never
touches the existing handle_message() text-state router. Badge-awarding
logic itself lives in achievements_storage.py (called from quiz_user.py /
initiatives_admin.py at the right moments); this module only displays.
"""
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import achievements_storage as ach

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
        logger.warning("achievements_user _edit failed: %s", e)


def format_badges(badges: list) -> str:
    if not badges:
        return "🏅 لا توجد أوسمة بعد.\n\nواصل المشاركة لتحصل على أول وسام لك!"
    lines = ["🏅 *إنجازاتك*\n"]
    for b in sorted(badges, key=lambda x: x.get("awarded_at", ""), reverse=True):
        try:
            dt = datetime.fromisoformat(b.get("awarded_at", ""))
            date_str = dt.strftime("%Y-%m-%d")
        except Exception:
            date_str = "—"
        lines.append(
            f"\n{b.get('icon', '🏅')} *{b.get('name', '—')}*\n"
            f"{b.get('description') or ''}\n"
            f"📅 {date_str}"
        )
    return "\n".join(lines)


async def handle_menu_achievements(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    badges = ach.get_user_badges(query.from_user.id)
    await _edit(update, format_badges(badges), _back_kb(BACK_TO_MAIN))
