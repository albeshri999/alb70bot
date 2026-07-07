# -*- coding: utf-8 -*-
import random
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from storage import get_user, create_user, update_user, load_days, load_users, get_hint_cost
from auth import check_password, is_locked
from config import ADMIN_ID
from credits import get_balance, deduct_credits, redeem_code, hint_mask
from utils import get_stage_question, get_stage_answer, default_question

logger = logging.getLogger(__name__)


# ── Keyboards ─────────────────────────────────────────────────────────────────

def _main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏆 الدخول للمسابقة", callback_data="menu_competition")],
        [InlineKeyboardButton("💳 شحن الرصيد",      callback_data="menu_credit"),
         InlineKeyboardButton("💰 معرفة الرصيد",    callback_data="menu_balance")],
    ])


def _back_to_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")
    ]])


def _hint_kb() -> InlineKeyboardMarkup:
    cost = get_hint_cost()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"💡 كشف حرف ({cost} نقطة)", callback_data="hint_reveal")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_days")],
    ])


def build_day_keyboard() -> InlineKeyboardMarkup:
    days = load_days()
    buttons = [
        [InlineKeyboardButton(data["name"], callback_data=f"day_{key}")]
        for key, data in sorted(days.items())
    ]
    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")])
    return InlineKeyboardMarkup(buttons)


# ── Hint helpers ──────────────────────────────────────────────────────────────

BAN_MESSAGE = "🚫 تم حظر حسابك من قبل إدارة المسابقة."


def _ban_text(user: dict) -> str:
    reason = (user or {}).get("ban_reason") or ""
    return BAN_MESSAGE + (f"\n\nالسبب: {reason}" if reason else "")


def _is_banned(user: dict) -> bool:
    return bool((user or {}).get("banned"))


def _get_hint_state(user: dict, step: int) -> set:
    if user.get("hint_step", -1) == step:
        return set(user.get("hint_revealed", []))
    return set()


def _prompt_text(day_data: dict, step: int, user: dict) -> str:
    password = get_stage_answer(day_data, step)
    revealed = _get_hint_state(user, step)
    mask     = hint_mask(password, revealed)
    question = get_stage_question(day_data, step)
    balance  = get_balance(int(user["user_id"]))
    return f"`{mask}`\n\n{question}\n\n💳 رصيدك: *{balance}* نقطة"


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id  = update.effective_user.id
    tg_user  = update.effective_user
    user     = get_user(user_id)

    if not user:
        create_user(user_id)
        update_user(user_id, username=tg_user.username or "")
        await update.message.reply_text(
            "مرحباً بك في المسابقة 🎉\n\nالرجاء إدخال اسمك الثلاثي:"
        )
        return

    if _is_banned(user):
        await update.message.reply_text(_ban_text(user))
        return

    locked, remaining = is_locked(user_id)
    if locked:
        await update.message.reply_text(
            f"🚫 أنت موقوف. يمكنك المحاولة بعد: *{remaining}*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    update_user(
        user_id,
        username=tg_user.username or "",
        state="main_menu",
        selected_day=None,
        password_step=0,
        completed=False,
        hint_step=-1,
        hint_revealed=[],
        last_seen=datetime.utcnow().isoformat(),
    )
    name    = user.get("full_name") or tg_user.first_name or ""
    balance = get_balance(user_id)
    await update.message.reply_text(
        f"أهلاً *{name}*!\n\n💳 رصيدك: *{balance}* نقطة\n\nاختر من القائمة:",
        reply_markup=_main_menu_kb(),
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Message router ────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text    = update.message.text.strip()
    user    = get_user(user_id)

    if not user:
        await update.message.reply_text("الرجاء استخدام /start للبدء.")
        return

    if _is_banned(user):
        await update.message.reply_text(_ban_text(user))
        return

    locked, remaining = is_locked(user_id)
    if locked:
        await update.message.reply_text(
            f"🚫 أنت موقوف. يمكنك المحاولة بعد: *{remaining}*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    state = user.get("state", "awaiting_name")

    if state == "awaiting_name":
        await _handle_name(update, user_id, text)
    elif state == "main_menu":
        balance = get_balance(user_id)
        await update.message.reply_text(
            f"💳 رصيدك: *{balance}* نقطة\n\nاختر من القائمة:",
            reply_markup=_main_menu_kb(),
            parse_mode=ParseMode.MARKDOWN,
        )
    elif state == "awaiting_code":
        await _handle_code_input(update, user_id, text)
    elif state == "awaiting_day":
        await update.message.reply_text(
            "الرجاء اختيار اليوم من الأزرار أدناه.",
            reply_markup=build_day_keyboard(),
        )
    elif state == "awaiting_password":
        await _handle_password(update, user_id, text, context)
    elif state == "locked":
        update_user(user_id, state="awaiting_password")
        user     = get_user(user_id)
        step     = user.get("password_step", 0)
        days     = load_days()
        day_data = days.get(str(user.get("selected_day", "")), {})
        ptext    = _prompt_text(day_data, step, user)
        await update.message.reply_text(
            f"🔓 انتهى الإيقاف.\n\n{ptext}",
            reply_markup=_hint_kb(),
            parse_mode=ParseMode.MARKDOWN,
        )
    elif state == "completed":
        await update.message.reply_text(
            "✅ لقد أكملت هذا اليوم بالفعل.\n\nاستخدم /start للعودة للقائمة الرئيسية."
        )
    else:
        await update.message.reply_text("الرجاء استخدام /start للبدء.")


# ── Name entry ────────────────────────────────────────────────────────────────

async def _handle_name(update: Update, user_id: int, text: str) -> None:
    if len(text) < 2:
        await update.message.reply_text("⚠️ الرجاء إدخال اسم ثلاثي صحيح.")
        return
    update_user(user_id, full_name=text, state="main_menu")
    balance = get_balance(user_id)
    await update.message.reply_text(
        f"أهلاً *{text}*! 🎉\n\n💳 رصيدك: *{balance}* نقطة\n\nاختر من القائمة:",
        reply_markup=_main_menu_kb(),
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Code redemption ───────────────────────────────────────────────────────────

async def _handle_code_input(update: Update, user_id: int, text: str) -> None:
    result, balance, pts = redeem_code(user_id, text)
    if result == "ok":
        msg = (
            f"✅ تم شحن رصيدك بنجاح.\n"
            f"تمت إضافة *{pts}* نقطة.\n\n"
            f"💳 رصيدك الحالي: *{balance}* نقطة"
        )
    elif result == "used":
        msg = "❌ تم استخدام هذا الكود مسبقًا."
    else:
        msg = "❌ كود الشحن غير صحيح."
    update_user(user_id, state="main_menu")
    await update.message.reply_text(
        msg, reply_markup=_main_menu_kb(), parse_mode=ParseMode.MARKDOWN,
    )


# ── Day selection ─────────────────────────────────────────────────────────────

async def handle_day_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    user_id = query.from_user.id

    _u = get_user(user_id)
    if _is_banned(_u):
        await query.answer(BAN_MESSAGE, show_alert=True)
        return

    locked, remaining = is_locked(user_id)
    if locked:
        await query.answer(f"🚫 أنت موقوف. يمكنك المحاولة بعد: {remaining}", show_alert=True)
        return

    await query.answer()
    day_key = query.data.replace("day_", "")
    days    = load_days()
    if day_key not in days:
        await query.edit_message_text("اليوم غير موجود. استخدم /start للمحاولة مجدداً.")
        return

    user = get_user(user_id)
    if not user:
        await query.edit_message_text("الرجاء استخدام /start للبدء.")
        return

    update_user(
        user_id,
        selected_day=day_key,
        password_step=0,
        state="awaiting_password",
        hint_step=-1,
        hint_revealed=[],
        day_started_at=datetime.utcnow().isoformat(),
    )

    day_data = days[day_key]
    ptext    = _prompt_text(day_data, 0, get_user(user_id))
    await query.edit_message_text(
        f"اخترت: *{day_data['name']}*\n\n{ptext}",
        reply_markup=_hint_kb(),
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Password handling ─────────────────────────────────────────────────────────

def _format_duration(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    parts = []
    if h:
        parts.append(f"{h} ساعة")
    if m:
        parts.append(f"{m} دقيقة")
    if s or not parts:
        parts.append(f"{s} ثانية")
    return " و ".join(parts)


async def _handle_password(update: Update, user_id: int, text: str,
                            context: ContextTypes.DEFAULT_TYPE | None = None) -> None:
    user        = get_user(user_id)
    day_key     = str(user.get("selected_day", ""))
    step        = user.get("password_step", 0)
    days        = load_days()
    day_data    = days.get(day_key, {})
    total_steps = len(day_data.get("stages", []))

    success, message = check_password(user_id, text)

    if success:
        next_step = step + 1
        if next_step >= total_steps:
            final_word   = day_data.get("final_word", "")
            completed_at = datetime.utcnow()
            update_user(
                user_id,
                password_step=next_step,
                state="completed",
                completed=True,
                completed_at=completed_at.isoformat(),
                password_attempts=0,
                hint_step=-1,
                hint_revealed=[],
            )
            await update.message.reply_text(
                f"🎉 أحسنت ومبارك!\n\nالكلمة النهائية هي:\n\n*{final_word}*\n\n"
                f"استخدم /start للعودة للقائمة الرئيسية.",
                parse_mode=ParseMode.MARKDOWN,
            )
            if context:
                try:
                    await _send_admin_notification(
                        context, user_id, user, day_key, day_data,
                        final_word, completed_at,
                    )
                except Exception as _e:
                    logger.warning("Admin notification failed: %s", _e)
        else:
            update_user(user_id, password_step=next_step, hint_step=-1, hint_revealed=[])
            ptext = _prompt_text(day_data, next_step, get_user(user_id))
            await update.message.reply_text(
                f"✅ صحيح!\n\n{ptext}",
                reply_markup=_hint_kb(),
                parse_mode=ParseMode.MARKDOWN,
            )
    else:
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


async def _send_admin_notification(context: ContextTypes.DEFAULT_TYPE,
                                    user_id: int, user: dict,
                                    day_key: str, day_data: dict,
                                    final_word: str, completed_at: datetime) -> None:
    """Send a real-time completion notification to the admin."""
    if not ADMIN_ID:
        return

    # Duration
    started_str  = user.get("day_started_at", "")
    if started_str:
        try:
            started      = datetime.fromisoformat(started_str)
            delta_secs   = max(0, int((completed_at - started).total_seconds()))
            duration_str = _format_duration(delta_secs)
        except Exception:
            duration_str = "—"
    else:
        duration_str = "—"

    # Rank: count completers for this day (updated user is already in storage)
    all_users = load_users()
    rank = sum(
        1 for u in all_users.values()
        if str(u.get("selected_day", "")) == day_key and u.get("completed", False)
    )

    def _esc(t: str) -> str:
        for ch in ("_", "*", "`", "[", "]"):
            t = t.replace(ch, f"\\{ch}")
        return t

    name     = _esc(user.get("full_name", "—") or "—")
    username = f"@{_esc(user['username'])}" if user.get("username") else "—"
    balance  = get_balance(user_id)
    day_name = _esc(day_data.get("name", f"يوم {day_key}"))
    ach_date = completed_at.strftime("%d/%m/%Y")
    ach_time = completed_at.strftime("%I:%M %p")

    text = (
        "🎉 *متسابق جديد أكمل المسابقة!*\n\n"
        f"👤 *الاسم:*\n{name}\n\n"
        f"📱 *اسم المستخدم:*\n{username}\n\n"
        f"🆔 *معرف تيليجرام:*\n`{user_id}`\n\n"
        f"📅 *المسابقة:*\n{day_name}\n\n"
        f"🏆 *الكلمة النهائية:*\n*{_esc(final_word)}*\n\n"
        f"⏱ *مدة الإنجاز:*\n{duration_str}\n\n"
        f"🕒 *وقت الإنجاز:*\n{ach_date}\n{ach_time}\n\n"
        f"🏅 *ترتيب الإنجاز:*\n#{rank}\n\n"
        f"💳 *الرصيد المتبقي:*\n{balance} نقطة"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 عرض بيانات المشارك", callback_data=f"notif_user_{user_id}")],
        [InlineKeyboardButton("📜 سجل حركة الرصيد",   callback_data=f"notif_tlog_{user_id}")],
        [InlineKeyboardButton("🏆 النتائج",              callback_data=f"notif_results_{day_key}")],
    ])

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb,
    )


# ── Notification button handlers (admin-only, standalone) ─────────────────────

async def handle_notif_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show quick participant summary when admin taps notification button."""
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    uid  = query.data[len("notif_user_"):]
    user = get_user(int(uid))
    if not user:
        await query.message.reply_text(f"❌ لم يتم العثور على المشارك `{uid}`.",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    days     = load_days()
    day_key  = str(user.get("selected_day") or "")
    day_name = days.get(day_key, {}).get("name", "لم يدخل بعد") if day_key else "لم يدخل بعد"
    created  = (user.get("created_at") or "—")[:16].replace("T", " ")
    comp_at  = (user.get("completed_at") or "—")[:16].replace("T", " ")
    uname    = f"@{user['username']}" if user.get("username") else "—"
    balance  = user.get("credits", 0)
    text = (
        "👤 *بيانات المشارك*\n\n"
        f"*الاسم:* {user.get('full_name','—')}\n"
        f"*المعرّف:* {uname}\n"
        f"*Telegram ID:* `{uid}`\n"
        f"*الرصيد:* {balance} نقطة\n"
        f"*تاريخ التسجيل:* {created}\n"
        f"*آخر يوم:* {day_name}\n"
        f"*وقت الإكمال:* {comp_at}"
    )
    await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def handle_notif_tlog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show last 5 transactions for the participant."""
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    uid = query.data[len("notif_tlog_"):]
    try:
        from transactions import load_user_txns, format_entry
        txns = load_user_txns(int(uid))
    except Exception:
        txns = []
    if not txns:
        await query.message.reply_text("📭 لا توجد عمليات مسجلة لهذا المشارك.")
        return
    recent = txns[-5:][::-1]
    sep    = "\n\n" + "─" * 20 + "\n\n"
    user   = get_user(int(uid))
    name   = user.get("full_name", uid) if user else uid
    text   = f"📜 *آخر عمليات {name}*\n\n" + sep.join(format_entry(t) for t in recent)
    if len(txns) > 5:
        text += f"\n\n_وغيرها {len(txns) - 5} عملية — افتح /admin للمزيد_"
    await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def handle_notif_results(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show completion results for the day."""
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    day_key  = query.data[len("notif_results_"):]
    days     = load_days()
    day_data = days.get(day_key, {})
    if not day_data:
        await query.message.reply_text("❌ اليوم غير موجود.")
        return
    all_users  = load_users()
    completers = [
        u for u in all_users.values()
        if str(u.get("selected_day", "")) == day_key and u.get("completed", False)
    ]
    day_name = day_data.get("name", f"يوم {day_key}")
    lines    = [f"🏆 *نتائج {day_name}*\n*{len(completers)}* مشارك أكمل\n"]
    for i, u in enumerate(completers, 1):
        lines.append(f"{i}. {u.get('full_name','—')} | 💳 {u.get('credits', 0)} نقطة")
    await query.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ── Main menu callbacks ───────────────────────────────────────────────────────

async def handle_menu_competition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    user_id = query.from_user.id
    user    = get_user(user_id)
    if not user:
        await query.answer("الرجاء استخدام /start للبدء.", show_alert=True)
        return
    if _is_banned(user):
        await query.answer(BAN_MESSAGE, show_alert=True)
        return
    locked, remaining = is_locked(user_id)
    if locked:
        await query.answer(f"🚫 أنت موقوف حتى: {remaining}", show_alert=True)
        return
    await query.answer()
    update_user(user_id, state="awaiting_day")
    await query.edit_message_text(
        "اختر المسابقة التي تريد المشاركة فيها:",
        reply_markup=build_day_keyboard(),
    )


async def handle_menu_credit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    user_id = query.from_user.id
    user    = get_user(user_id)
    if not user:
        await query.answer("الرجاء استخدام /start للبدء.", show_alert=True)
        return
    if _is_banned(user):
        await query.answer(BAN_MESSAGE, show_alert=True)
        return
    await query.answer()
    update_user(user_id, state="awaiting_code")
    await query.edit_message_text("💳 أدخل كود الشحن:", reply_markup=_back_to_main_kb())


async def handle_menu_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    _u = get_user(query.from_user.id)
    if _is_banned(_u):
        await query.answer(BAN_MESSAGE, show_alert=True)
        return
    await query.answer()
    balance = get_balance(query.from_user.id)
    await query.edit_message_text(
        f"💰 رصيدك الحالي:\n\n*{balance}* نقطة",
        reply_markup=_back_to_main_kb(),
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Hint callback ─────────────────────────────────────────────────────────────

async def handle_hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    user_id = query.from_user.id
    user    = get_user(user_id)

    if _is_banned(user):
        await query.answer(BAN_MESSAGE, show_alert=True)
        return

    if not user or user.get("state") != "awaiting_password":
        await query.answer("💡 الكشف متاح فقط أثناء المسابقة.", show_alert=True)
        return

    locked, _ = is_locked(user_id)
    if locked:
        await query.answer("🚫 أنت موقوف.", show_alert=True)
        return

    hint_cost = get_hint_cost()
    if get_balance(user_id) < hint_cost:
        await query.answer("❌ رصيدك غير كافٍ لكشف حرف.", show_alert=True)
        return

    day_key  = str(user.get("selected_day", ""))
    step     = user.get("password_step", 0)
    days     = load_days()
    day_data = days.get(day_key, {})
    stages   = day_data.get("stages", [])

    if step >= len(stages):
        await query.answer("حدث خطأ.", show_alert=True)
        return

    password   = stages[step].get("answer", "")
    revealed   = _get_hint_state(user, step)
    unrevealed = [i for i in range(len(password)) if i not in revealed]

    if not unrevealed:
        await query.answer("تم الكشف عن جميع الحروف بالفعل.", show_alert=True)
        return

    balance_before  = get_balance(user_id)
    ok, new_balance = deduct_credits(user_id, hint_cost)
    if not ok:
        await query.answer("❌ رصيدك غير كافٍ.", show_alert=True)
        return

    try:
        from transactions import record as _rec
        _rec(user_id, user.get("full_name", "—"),
             "hint_purchase", hint_cost, balance_before, new_balance,
             "كشف حرف")
    except Exception as _e:
        logger.warning("transaction record failed: %s", _e)

    new_idx = random.choice(unrevealed)
    revealed.add(new_idx)
    update_user(user_id, hint_step=step, hint_revealed=list(revealed))

    mask      = hint_mask(password, revealed)
    question  = get_stage_question(day_data, step)
    remaining = len(unrevealed) - 1
    text      = f"`{mask}`\n\n{question}\n\n💳 رصيدك: *{new_balance}* نقطة"
    kb        = _hint_kb() if remaining > 0 else None

    await query.answer()
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


# ── Back navigation callbacks ─────────────────────────────────────────────────

async def handle_back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    user_id = query.from_user.id
    user = get_user(user_id)
    if _is_banned(user):
        await query.answer(BAN_MESSAGE, show_alert=True)
        return
    await query.answer()
    if not user:
        await query.edit_message_text("الرجاء استخدام /start للبدء.")
        return
    update_user(user_id, state="main_menu")
    name    = user.get("full_name") or query.from_user.first_name or ""
    balance = get_balance(user_id)
    await query.edit_message_text(
        f"أهلاً *{name}*!\n\n💳 رصيدك: *{balance}* نقطة\n\nاختر من القائمة:",
        reply_markup=_main_menu_kb(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_back_to_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    user_id = query.from_user.id
    user = get_user(user_id)
    if _is_banned(user):
        await query.answer(BAN_MESSAGE, show_alert=True)
        return
    await query.answer()
    if not user:
        await query.edit_message_text("الرجاء استخدام /start للبدء.")
        return
    update_user(user_id, state="awaiting_day")
    await query.edit_message_text(
        "اختر المسابقة التي تريد المشاركة فيها:",
        reply_markup=build_day_keyboard(),
    )


# ── Admin utility commands ────────────────────────────────────────────────────

async def unlock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية لاستخدام هذا الأمر.")
        return
    if not context.args:
        await update.message.reply_text("الاستخدام: /unlock <user_id>")
        return
    target_id_str = context.args[0]
    if not target_id_str.isdigit():
        await update.message.reply_text("⚠️ معرّف المستخدم يجب أن يكون رقماً صحيحاً.")
        return
    target_id = int(target_id_str)
    target    = get_user(target_id)
    if not target:
        await update.message.reply_text(
            f"⚠️ لا يوجد مستخدم بالمعرف: `{target_id}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    update_user(target_id, locked_until=None, password_attempts=0, state="awaiting_password")
    await update.message.reply_text(
        f"✅ تم فك الإيقاف عن:\n*{target.get('full_name', '—')}* | `{target_id}`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def participants(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية لاستخدام هذا الأمر.")
        return
    users = load_users()
    if not users:
        await update.message.reply_text("لا يوجد مشاركون بعد.")
        return
    lines = ["📋 *قائمة المشاركين*\n"]
    for i, (uid, u) in enumerate(users.items(), 1):
        uname = f"@{u['username']}" if u.get("username") else "—"
        lines.append(f"{i}. {u.get('full_name', '—')}\n   {uname} | `{uid}`")
    lines.append(f"\nإجمالي: *{len(users)}*")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
