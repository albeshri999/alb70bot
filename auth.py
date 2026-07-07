# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
from storage import get_user, update_user, load_days
from config import MAX_PASSWORD_ATTEMPTS


def _lockout_label(hours: int) -> str:
    if hours == 1:
        return "ساعة واحدة"
    elif hours == 2:
        return "ساعتين"
    elif hours == 3:
        return "ثلاث ساعات"
    else:
        return f"{hours} ساعات"


def is_locked(user_id: int) -> tuple[bool, str]:
    user = get_user(user_id)
    locked_until = user.get("locked_until")
    if locked_until:
        locked_dt = datetime.fromisoformat(locked_until)
        now = datetime.utcnow()
        if now < locked_dt:
            remaining = locked_dt - now
            hours, remainder = divmod(int(remaining.total_seconds()), 3600)
            minutes = remainder // 60
            return True, f"{hours} ساعة و{minutes} دقيقة"
        else:
            update_user(user_id, locked_until=None, password_attempts=0)
    return False, ""


def check_password(user_id: int, password: str) -> tuple[bool, str]:
    locked, remaining = is_locked(user_id)
    if locked:
        return False, (
            f"🚫 أنت موقوف. يمكنك المحاولة بعد: *{remaining}*"
        )

    user = get_user(user_id)
    day_key = str(user.get("selected_day"))
    step = user.get("password_step", 0)

    days = load_days()
    day_data = days.get(day_key, {})
    stages = day_data.get("stages", [])
    lockout_hours = day_data.get("lockout_hours", 3)

    if step >= len(stages):
        return False, "حدث خطأ. استخدم /start للبدء من جديد."

    correct = stages[step].get("answer", "")

    if password.strip() == correct:
        update_user(user_id, password_attempts=0)
        return True, ""

    attempts = user.get("password_attempts", 0) + 1
    remaining_attempts = MAX_PASSWORD_ATTEMPTS - attempts

    if attempts >= MAX_PASSWORD_ATTEMPTS:
        lock_until = (datetime.utcnow() + timedelta(hours=lockout_hours)).isoformat()
        update_user(user_id, password_attempts=attempts, locked_until=lock_until, state="locked")
        return False, "🚫 تم إيقافك لمدة 3 ساعات بسبب إدخال كلمة السر بشكل خاطئ ثلاث مرات."

    update_user(user_id, password_attempts=attempts)
    return False, (
        f"❌ كلمة السر غير صحيحة.\n"
        f"المحاولات المتبقية: *{remaining_attempts}*"
    )
