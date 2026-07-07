# -*- coding: utf-8 -*-
import json
import os
from datetime import datetime
from config import USERS_FILE, DAYS_FILE, CODES_FILE, CONFIG_FILE, CREDIT_LOG_FILE, ADMIN_LOG_FILE


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


def load_users() -> dict:
    return _load_json(USERS_FILE, {})


def save_users(users: dict) -> None:
    _save_json(USERS_FILE, users)


def load_days() -> dict:
    return _load_json(DAYS_FILE, {})


def save_days(days: dict) -> None:
    _save_json(DAYS_FILE, days)


def load_codes() -> dict:
    return _load_json(CODES_FILE, {})


def save_codes(codes: dict) -> None:
    _save_json(CODES_FILE, codes)


def migrate_days() -> None:
    """Convert old {passwords, prompts} format to new {stages} format (one-time, safe to re-run)."""
    days = load_days()
    changed = False
    for key, d in days.items():
        if "stages" not in d and "passwords" in d:
            passwords = d.pop("passwords", [])
            prompts   = d.pop("prompts", [])
            d["stages"] = [
                {"question": prompts[i] if i < len(prompts) else "", "answer": pw}
                for i, pw in enumerate(passwords)
            ]
            changed = True
    if changed:
        save_days(days)


def load_config() -> dict:
    return _load_json(CONFIG_FILE, {})


def save_config(cfg: dict) -> None:
    _save_json(CONFIG_FILE, cfg)


def get_hint_cost() -> int:
    return load_config().get("hint_cost", 5)


def set_hint_cost(cost: int) -> None:
    cfg = load_config()
    cfg["hint_cost"] = cost
    save_config(cfg)


def load_credit_log() -> list:
    return _load_json(CREDIT_LOG_FILE, [])


def save_credit_log(log: list) -> None:
    _save_json(CREDIT_LOG_FILE, log)


def log_credit_action(admin_id: int, user_id: str, user_name: str,
                      action: str, amount: int, new_balance: int) -> None:
    log = load_credit_log()
    log.append({
        "timestamp": datetime.utcnow().isoformat(),
        "admin_id":  admin_id,
        "user_id":   str(user_id),
        "user_name": user_name,
        "action":    action,      # "add" | "remove" | "reset"
        "amount":    amount,
        "new_balance": new_balance,
    })
    save_credit_log(log)


def get_user(user_id: int) -> dict:
    users = load_users()
    return users.get(str(user_id), {})


def save_user(user_id: int, data: dict) -> None:
    users = load_users()
    users[str(user_id)] = data
    save_users(users)


def create_user(user_id: int) -> dict:
    user = {
        "user_id": user_id,
        "full_name": "",
        "state": "awaiting_name",
        "selected_day": None,
        "password_step": 0,
        "password_attempts": 0,
        "locked_until": None,
        "completed": False,
        "created_at": datetime.utcnow().isoformat(),
    }
    save_user(user_id, user)
    return user


def update_user(user_id: int, **kwargs) -> dict:
    user = get_user(user_id)
    user.update(kwargs)
    save_user(user_id, user)
    return user


def load_admin_log() -> list:
    return _load_json(ADMIN_LOG_FILE, [])


def save_admin_log(log: list) -> None:
    _save_json(ADMIN_LOG_FILE, log)


def append_admin_log(entry: dict) -> None:
    log = load_admin_log()
    log.append(entry)
    save_admin_log(log)


def rename_user(user_id: int, new_name: str) -> str:
    """Update full_name in users.json. Returns the old name."""
    uid   = str(user_id)
    users = load_users()
    old   = (users.get(uid) or {}).get("full_name", "—")
    if uid in users:
        users[uid]["full_name"] = new_name
        save_users(users)
    return old


def delete_user(user_id: int) -> bool:
    """Permanently remove a user from users.json and credit_log.json."""
    uid   = str(user_id)
    users = load_users()
    existed = uid in users
    if existed:
        del users[uid]
        save_users(users)

    log = load_credit_log()
    new_log = [e for e in log if str(e.get("user_id")) != uid]
    if len(new_log) != len(log):
        save_credit_log(new_log)

    return existed
