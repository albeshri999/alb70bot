# -*- coding: utf-8 -*-
"""
Admin side of the fully independent '📖 إدارة الكلمات' (Word-Delivery) system.

Its OWN ConversationHandler, entered from the admin main menu via the
"adm_words" callback button (the only line added to admin.py). All data
lives in words_storage.py (its own JSON files) — nothing here touches
days.json/users.json stages, quizzes.json, initiatives.json, etc. The only
existing data it reads (never writes) is storage.load_days(), purely to
know how many competition days exist and their display names, as required
by the feature spec ("حسب عدد أيام المسابقة الموجود في الإعدادات").

Point awards on "✅ تم إلقاء الكلمة" go through the existing credits.py
helper, exactly like the quiz/initiatives crediting flow.
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler,
    MessageHandler, CallbackQueryHandler, filters,
)
from telegram.constants import ParseMode

import admins_store
from storage import load_days
import words_storage as ws
import credits

logger = logging.getLogger(__name__)

# ── States ────────────────────────────────────────────────────────────────────
(WD_HUB,
 WD_ADD_TEXT, WD_ADD_DAY, WD_ADD_POINTS,
 WD_WORDS_DAYS, WD_WORDS_LIST, WD_WORD_ACTIONS, WD_EDIT_TEXT, WD_DEL_CONFIRM,
 WD_OPEN_DAY, WD_CLOSE_DAY,
 WD_VOLUNTEERS_DAYS, WD_VOLUNTEERS_LIST, WD_MANUAL_PICK,
 WD_ASSIGN_CONFIRM,
 ) = range(15)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_admin(uid: int) -> bool:
    return admins_store.is_admin(uid)


async def _reply(update: Update, text: str, keyboard=None):
    kw = {"text": text, "parse_mode": ParseMode.MARKDOWN}
    if keyboard:
        kw["reply_markup"] = keyboard
    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(**kw)
        except Exception as e1:
            logger.warning("words_admin _reply edit_text failed: %s", e1)
            try:
                await update.callback_query.message.reply_text(**kw)
            except Exception as e2:
                logger.error("words_admin _reply reply_text also failed: %s", e2)
    else:
        await update.message.reply_text(**kw)


def _day_name(day_key: str) -> str:
    return load_days().get(str(day_key), {}).get("name", f"اليوم {day_key}")


def _days_kb(prefix: str, back_cb: str = "wd_hub") -> InlineKeyboardMarkup:
    days = load_days()
    rows = [
        [InlineKeyboardButton(d.get("name", k), callback_data=f"{prefix}_{k}")]
        for k, d in sorted(days.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0)
    ]
    if not rows:
        rows = [[InlineKeyboardButton("لا توجد أيام بعد", callback_data="wd_noop")]]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)


# ── Hub ───────────────────────────────────────────────────────────────────────

def _hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة كلمة", callback_data="wd_add")],
        [InlineKeyboardButton("📚 الكلمات", callback_data="wd_words")],
        [InlineKeyboardButton("📢 فتح الإعلان", callback_data="wd_open")],
        [InlineKeyboardButton("🔒 إغلاق الإعلان", callback_data="wd_close")],
        [InlineKeyboardButton("👥 الراغبون", callback_data="wd_volunteers")],
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="wd_stats")],
        [InlineKeyboardButton("⬅ رجوع", callback_data="adm_main")],
    ])


async def wd_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer("⛔ ليس لديك صلاحية.", show_alert=True)
        return ConversationHandler.END
    if update.callback_query:
        await update.callback_query.answer()
    for k in ("wd_text", "wd_day", "wd_word_id", "wd_target_uid"):
        context.user_data.pop(k, None)
    await _reply(update, "📖 *إدارة الكلمات*\n\nاختر العملية:", _hub_kb())
    return WD_HUB


async def wd_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


# ── Add word ──────────────────────────────────────────────────────────────────

async def wd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(
        update,
        "✍️ أرسل نص الكلمة كاملاً (قد تكون كلمة قصيرة، خطبة، قصة أو أي نص طويل).",
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="wd_hub")]]),
    )
    return WD_ADD_TEXT


async def wd_add_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if not text.strip():
        await update.message.reply_text("⚠️ الرجاء إرسال نص صالح.")
        return WD_ADD_TEXT
    context.user_data["wd_text"] = text
    days = load_days()
    if not days:
        await update.message.reply_text(
            "⚠️ لا توجد أيام مسابقة معرّفة في الإعدادات بعد.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="wd_hub")]]),
        )
        return WD_HUB
    await update.message.reply_text(
        "📅 اختر اليوم الذي ستلقى فيه هذه الكلمة:",
        reply_markup=_days_kb("wdadd_day"),
    )
    return WD_ADD_DAY


async def wd_add_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    day_key = query.data.replace("wdadd_day_", "")
    context.user_data["wd_day"] = day_key
    await _reply(
        update, "🏆 اختر عدد النقاط لهذه الكلمة (أرسل رقماً):",
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="wd_hub")]]),
    )
    return WD_ADD_POINTS


async def wd_add_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    if not raw.isdigit():
        await update.message.reply_text("⚠️ الرجاء إرسال رقم صحيح.")
        return WD_ADD_POINTS
    day_key = context.user_data.get("wd_day")
    text = context.user_data.get("wd_text", "")
    ws.add_word(day_key, text, int(raw))
    await update.message.reply_text(
        f"✅ تم حفظ الكلمة وربطها بـ {_day_name(day_key)} بنجاح.",
        reply_markup=_hub_kb(),
    )
    return WD_HUB


# ── Words list / manage ───────────────────────────────────────────────────────

async def wd_words(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    days = load_days()
    words = ws.load_words()
    rows = []
    for k, d in sorted(days.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
        count = len(words.get(k, []))
        rows.append([InlineKeyboardButton(f"{d.get('name', k)} ({count} كلمات)", callback_data=f"wdlist_day_{k}")])
    if not rows:
        rows = [[InlineKeyboardButton("لا توجد أيام بعد", callback_data="wd_noop")]]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="wd_hub")])
    await _reply(update, "📚 *الكلمات*\n\nاختر اليوم:", InlineKeyboardMarkup(rows))
    return WD_WORDS_DAYS


def _words_list_kb(day_key: str) -> InlineKeyboardMarkup:
    day_words = ws.words_for_day(day_key)
    rows = []
    for w in day_words:
        label = (w.get("text") or "")[:25].replace("\n", " ")
        rows.append([InlineKeyboardButton(f"📝 {label}", callback_data=f"wdw_{day_key}_{w['id']}")])
    if not day_words:
        rows.append([InlineKeyboardButton("لا توجد كلمات لهذا اليوم", callback_data="wd_noop")])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="wd_words")])
    return InlineKeyboardMarkup(rows)


async def wd_words_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    day_key = query.data.replace("wdlist_day_", "")
    context.user_data["wd_day"] = day_key
    await _reply(update, f"📚 *كلمات {_day_name(day_key)}*", _words_list_kb(day_key))
    return WD_WORDS_LIST


async def wd_word_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, day_key, word_id = query.data.split("_", 2)
    context.user_data["wd_day"] = day_key
    context.user_data["wd_word_id"] = word_id
    word = ws.get_word(day_key, word_id)
    status_labels = {"available": "متاحة", "reserved": "محجوزة", "used": "مستخدمة"}
    text = (
        f"📝 *الكلمة*\n\n{word.get('text', '—')}\n\n"
        f"🏆 النقاط: {word.get('points', 0)}\n"
        f"📍 الحالة: {status_labels.get(word.get('status'), '—')}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 عرض", callback_data="wd_view_noop"),
         InlineKeyboardButton("✏ تعديل", callback_data="wd_edit_word")],
        [InlineKeyboardButton("🗑 حذف", callback_data="wd_del_word")],
        [InlineKeyboardButton("🔙 رجوع", callback_data=f"wdlist_day_{day_key}")],
    ])
    await _reply(update, text, kb)
    return WD_WORD_ACTIONS


async def wd_view_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return WD_WORD_ACTIONS


async def wd_edit_word_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(
        update, "✍️ أرسل النص الجديد للكلمة:",
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="wd_hub")]]),
    )
    return WD_EDIT_TEXT


async def wd_edit_word_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if not text.strip():
        await update.message.reply_text("⚠️ الرجاء إرسال نص صالح.")
        return WD_EDIT_TEXT
    day_key = context.user_data.get("wd_day")
    word_id = context.user_data.get("wd_word_id")
    ws.update_word(day_key, word_id, text=text)
    await update.message.reply_text("✅ تم تعديل الكلمة.", reply_markup=_words_list_kb(day_key))
    return WD_WORDS_LIST


async def wd_del_word_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ نعم، احذف", callback_data="wd_del_yes"),
        InlineKeyboardButton("❌ إلغاء", callback_data="wd_del_no"),
    ]])
    await _reply(update, "⚠️ هل تريد حذف هذه الكلمة نهائياً؟", kb)
    return WD_DEL_CONFIRM


async def wd_del_word_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    day_key = context.user_data.get("wd_day")
    word_id = context.user_data.get("wd_word_id")
    ws.delete_word(day_key, word_id)
    await _reply(update, "🗑 تم حذف الكلمة.", _words_list_kb(day_key))
    return WD_WORDS_LIST


async def wd_del_word_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    day_key = context.user_data.get("wd_day")
    return await wd_word_pick_replay(update, context, day_key)


async def wd_word_pick_replay(update, context, day_key):
    word_id = context.user_data.get("wd_word_id")
    word = ws.get_word(day_key, word_id)
    if not word:
        await _reply(update, "📚 *الكلمات*", _words_list_kb(day_key))
        return WD_WORDS_LIST
    status_labels = {"available": "متاحة", "reserved": "محجوزة", "used": "مستخدمة"}
    text = (
        f"📝 *الكلمة*\n\n{word.get('text', '—')}\n\n"
        f"🏆 النقاط: {word.get('points', 0)}\n"
        f"📍 الحالة: {status_labels.get(word.get('status'), '—')}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 عرض", callback_data="wd_view_noop"),
         InlineKeyboardButton("✏ تعديل", callback_data="wd_edit_word")],
        [InlineKeyboardButton("🗑 حذف", callback_data="wd_del_word")],
        [InlineKeyboardButton("🔙 رجوع", callback_data=f"wdlist_day_{day_key}")],
    ])
    await _reply(update, text, kb)
    return WD_WORD_ACTIONS


# ── Open / Close announcement ────────────────────────────────────────────────

async def wd_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "📢 اختر اليوم لفتح إعلان إلقاء الكلمات:", _days_kb("wdopen_day"))
    return WD_OPEN_DAY


async def wd_open_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    day_key = query.data.replace("wdopen_day_", "")
    if not ws.available_words(day_key):
        await query.answer("⚠️ لا توجد كلمات متاحة لهذا اليوم بعد.", show_alert=True)
        return WD_OPEN_DAY
    ws.open_announcement(day_key)
    await _reply(update, f"✅ تم فتح إعلان إلقاء الكلمات لـ {_day_name(day_key)}.", _hub_kb())
    return WD_HUB


async def wd_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await _reply(update, "🔒 اختر اليوم لإغلاق إعلان إلقاء الكلمات:", _days_kb("wdclose_day"))
    return WD_CLOSE_DAY


async def wd_close_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    day_key = query.data.replace("wdclose_day_", "")
    ws.close_announcement(day_key)
    await _reply(update, f"✅ تم إغلاق إعلان إلقاء الكلمات لـ {_day_name(day_key)}.", _hub_kb())
    return WD_HUB


# ── Volunteers ────────────────────────────────────────────────────────────────

async def wd_volunteers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    days = load_days()
    rows = []
    for k, d in sorted(days.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
        count = len(ws.volunteers_for_day(k))
        rows.append([InlineKeyboardButton(f"{d.get('name', k)} ({count} راغب)", callback_data=f"wdvol_day_{k}")])
    if not rows:
        rows = [[InlineKeyboardButton("لا توجد أيام بعد", callback_data="wd_noop")]]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="wd_hub")])
    await _reply(update, "👥 *الراغبون*\n\nاختر اليوم:", InlineKeyboardMarkup(rows))
    return WD_VOLUNTEERS_DAYS


def _volunteers_kb(day_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎲 اختيار عشوائي", callback_data=f"wdrand_{day_key}")],
        [InlineKeyboardButton("👤 اختيار من القائمة", callback_data=f"wdmanual_{day_key}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="wd_volunteers")],
    ])


async def wd_volunteers_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    day_key = query.data.replace("wdvol_day_", "")
    context.user_data["wd_day"] = day_key
    waiting = ws.waiting_volunteers(day_key)
    names = "\n".join(f"• {v.get('user_name')}" for v in waiting) or "لا يوجد راغبون بعد."
    text = f"👥 *الراغبون — {_day_name(day_key)}*\n\nالعدد: {len(waiting)}\n\n{names}"
    await _reply(update, text, _volunteers_kb(day_key))
    return WD_VOLUNTEERS_LIST


async def _do_assignment(update: Update, context: ContextTypes.DEFAULT_TYPE, day_key: str, volunteer: dict):
    word = ws.pick_random_available_word(day_key)
    if not word:
        await _reply(update, "⚠️ لا توجد كلمات متاحة لهذا اليوم.", _volunteers_kb(day_key))
        return WD_VOLUNTEERS_LIST

    ws.assign_word_to_volunteer(day_key, volunteer["user_id"], word["id"])
    context.user_data["wd_day"] = day_key
    context.user_data["wd_target_uid"] = volunteer["user_id"]

    try:
        await context.bot.send_message(
            chat_id=int(volunteer["user_id"]),
            text=f"🎤 كلمتك لـ {_day_name(day_key)}:\n\n{word['text']}",
        )
    except Exception as e:
        logger.warning("Failed to send word to volunteer %s: %s", volunteer.get("user_id"), e)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تم إلقاء الكلمة", callback_data="wd_deliver_yes"),
        InlineKeyboardButton("❌ إلغاء", callback_data="wd_deliver_no"),
    ]])
    await _reply(
        update,
        f"✅ تم إرسال الكلمة إلى *{volunteer.get('user_name')}*.\n\nبعد إلقائها اختر:",
        kb,
    )
    return WD_ASSIGN_CONFIRM


async def wd_random_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    day_key = query.data.replace("wdrand_", "")
    waiting = ws.waiting_volunteers(day_key)
    if not waiting:
        await query.answer("⚠️ لا يوجد راغبون بانتظار الاختيار.", show_alert=True)
        return WD_VOLUNTEERS_LIST
    import random as _r
    volunteer = _r.choice(waiting)
    return await _do_assignment(update, context, day_key, volunteer)


async def wd_manual_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    day_key = query.data.replace("wdmanual_", "")
    context.user_data["wd_day"] = day_key
    waiting = ws.waiting_volunteers(day_key)
    if not waiting:
        await query.answer("⚠️ لا يوجد راغبون بانتظار الاختيار.", show_alert=True)
        return WD_VOLUNTEERS_LIST
    rows = [
        [InlineKeyboardButton(v.get("user_name", "—"), callback_data=f"wdpick_{v['user_id']}")]
        for v in waiting
    ]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"wdvol_day_{day_key}")])
    await _reply(update, "👤 اختر متسابقاً:", InlineKeyboardMarkup(rows))
    return WD_MANUAL_PICK


async def wd_manual_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.data.replace("wdpick_", "")
    day_key = context.user_data.get("wd_day")
    volunteer = ws.get_volunteer(day_key, user_id)
    if not volunteer or volunteer.get("status") != ws.VOL_WAITING:
        await query.answer("⚠️ هذا المتسابق لم يعد متاحاً.", show_alert=True)
        return WD_VOLUNTEERS_LIST
    return await _do_assignment(update, context, day_key, volunteer)


async def wd_deliver_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    day_key = context.user_data.get("wd_day")
    user_id = context.user_data.get("wd_target_uid")
    volunteer = ws.get_volunteer(day_key, user_id)
    word_id = volunteer.get("assigned_word_id") if volunteer else None
    word = ws.get_word(day_key, word_id) if word_id else {}
    points = int(word.get("points", 0))

    day_exhausted = ws.confirm_delivery(day_key, user_id, points)
    if points:
        credits.add_credits(int(user_id), points)

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text=f"🎉 تم تسجيل إلقائك للكلمة! حصلت على {points} نقطة.",
        )
    except Exception as e:
        logger.warning("Failed to notify volunteer %s: %s", user_id, e)

    if day_exhausted:
        ws.close_announcement(day_key)

    msg = "✅ تم تسجيل إلقاء الكلمة ومنح النقاط."
    if day_exhausted:
        msg += f"\n\n📢 تم إغلاق إعلان {_day_name(day_key)} تلقائياً لانتهاء الكلمات."
    await _reply(update, msg, _hub_kb())
    return WD_HUB


async def wd_deliver_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    day_key = context.user_data.get("wd_day")
    user_id = context.user_data.get("wd_target_uid")
    ws.cancel_delivery(day_key, user_id)
    await _reply(update, "❌ تم الإلغاء. أعيدت الكلمة إلى القائمة المتاحة.", _hub_kb())
    return WD_HUB


# ── Statistics ────────────────────────────────────────────────────────────────

async def wd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    days = load_days()
    lines = ["📊 *الإحصائيات*"]
    for k, d in sorted(days.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
        s = ws.stats_for_day(k)
        lines.append(
            f"\n*{d.get('name', k)}*\n"
            f"عدد الكلمات: {s['total']}\n"
            f"المتبقية: {s['available']}\n"
            f"المستخدمة: {s['used']}\n"
            f"الراغبون: {s['volunteers']}\n"
            f"من ألقى: {s['delivered']}"
        )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="wd_hub")]])
    await _reply(update, "\n".join(lines), kb)
    return WD_HUB


# ── Handler assembly ──────────────────────────────────────────────────────────

def build_words_admin_handler() -> ConversationHandler:
    hub_reentry = CallbackQueryHandler(wd_hub, pattern="^wd_hub$")
    noop = CallbackQueryHandler(wd_noop, pattern="^wd_noop$")

    return ConversationHandler(
        entry_points=[CallbackQueryHandler(wd_hub, pattern="^adm_words$")],
        states={
            WD_HUB: [
                CallbackQueryHandler(wd_add,        pattern="^wd_add$"),
                CallbackQueryHandler(wd_words,       pattern="^wd_words$"),
                CallbackQueryHandler(wd_open,        pattern="^wd_open$"),
                CallbackQueryHandler(wd_close,       pattern="^wd_close$"),
                CallbackQueryHandler(wd_volunteers,  pattern="^wd_volunteers$"),
                CallbackQueryHandler(wd_stats,       pattern="^wd_stats$"),
            ],
            WD_ADD_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, wd_add_text), hub_reentry],
            WD_ADD_DAY: [
                CallbackQueryHandler(wd_add_day, pattern=r"^wdadd_day_\w+$"),
                hub_reentry,
            ],
            WD_ADD_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, wd_add_points), hub_reentry],
            WD_WORDS_DAYS: [
                CallbackQueryHandler(wd_words_day, pattern=r"^wdlist_day_\w+$"),
                noop, hub_reentry,
            ],
            WD_WORDS_LIST: [
                CallbackQueryHandler(wd_word_pick, pattern=r"^wdw_\w+_\w+$"),
                CallbackQueryHandler(wd_words,      pattern="^wd_words$"),
                noop, hub_reentry,
            ],
            WD_WORD_ACTIONS: [
                CallbackQueryHandler(wd_view_noop,        pattern="^wd_view_noop$"),
                CallbackQueryHandler(wd_edit_word_prompt,  pattern="^wd_edit_word$"),
                CallbackQueryHandler(wd_del_word_confirm,  pattern="^wd_del_word$"),
                CallbackQueryHandler(wd_words_day,         pattern=r"^wdlist_day_\w+$"),
                hub_reentry,
            ],
            WD_EDIT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, wd_edit_word_save), hub_reentry],
            WD_DEL_CONFIRM: [
                CallbackQueryHandler(wd_del_word_yes, pattern="^wd_del_yes$"),
                CallbackQueryHandler(wd_del_word_no,  pattern="^wd_del_no$"),
                hub_reentry,
            ],
            WD_OPEN_DAY: [
                CallbackQueryHandler(wd_open_day, pattern=r"^wdopen_day_\w+$"),
                hub_reentry,
            ],
            WD_CLOSE_DAY: [
                CallbackQueryHandler(wd_close_day, pattern=r"^wdclose_day_\w+$"),
                hub_reentry,
            ],
            WD_VOLUNTEERS_DAYS: [
                CallbackQueryHandler(wd_volunteers_day, pattern=r"^wdvol_day_\w+$"),
                noop, hub_reentry,
            ],
            WD_VOLUNTEERS_LIST: [
                CallbackQueryHandler(wd_random_pick,  pattern=r"^wdrand_\w+$"),
                CallbackQueryHandler(wd_manual_list,  pattern=r"^wdmanual_\w+$"),
                CallbackQueryHandler(wd_volunteers,   pattern="^wd_volunteers$"),
                hub_reentry,
            ],
            WD_MANUAL_PICK: [
                CallbackQueryHandler(wd_manual_pick,    pattern=r"^wdpick_\w+$"),
                CallbackQueryHandler(wd_volunteers_day, pattern=r"^wdvol_day_\w+$"),
                hub_reentry,
            ],
            WD_ASSIGN_CONFIRM: [
                CallbackQueryHandler(wd_deliver_yes, pattern="^wd_deliver_yes$"),
                CallbackQueryHandler(wd_deliver_no,  pattern="^wd_deliver_no$"),
                hub_reentry,
            ],
        },
        fallbacks=[hub_reentry, noop],
        per_message=False,
        name="words_admin_conversation",
    )
