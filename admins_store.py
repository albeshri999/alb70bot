# -*- coding: utf-8 -*-
"""
Multi-admin support — fully additive on top of the existing single-owner
ADMIN_ID system (config.py), and independent of every other storage file.

Model:
- The "owner" is whoever ADMIN_ID (env var ADMIN_TELEGRAM_ID) points to.
  Only the owner can add/remove other admins, and only the owner can see/use
  "⚙️ إعدادات المشرفين" — this keeps one person always in ultimate control.
- Extra admins are stored in their own JSON file (data/extra_admins.json),
  keyed by Telegram ID, with their name/username/added-at/added-by recorded
  for display in "👥 قائمة المشرفين".
- Extra admins get a RESTRICTED subset of the admin panel (see admin.py's
  _main_kb) — not full owner access.
"""
import json
import os
from datetime import datetime

from config import ADMIN_ID

EXTRA_ADMINS_FILE = "data/extra_admins.json"


def _load() -> dict:
    os.makedirs(os.path.dirname(EXTRA_ADMINS_FILE), exist_ok=True)
    if not os.path.exists(EXTRA_ADMINS_FILE):
        return {}
    with open(EXTRA_ADMINS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Backward compatibility: earlier version stored a plain list of ints.
    if isinstance(data, list):
        return {
            str(uid): {"telegram_id": int(uid), "name": "", "username": "",
                       "added_at": None, "added_by": None}
            for uid in data
        }
    return data


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(EXTRA_ADMINS_FILE), exist_ok=True)
    with open(EXTRA_ADMINS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_extra_admins() -> dict:
    """{'telegram_id_str': {telegram_id, name, username, added_at, added_by}}"""
    return _load()


def extra_admin_ids() -> list:
    return [int(k) for k in load_extra_admins().keys()]


def is_owner(user_id) -> bool:
    return bool(ADMIN_ID) and int(user_id) == int(ADMIN_ID)


def is_admin(user_id) -> bool:
    """True for the owner (ADMIN_ID) OR anyone added as an extra admin."""
    uid = int(user_id)
    if is_owner(uid):
        return True
    return str(uid) in load_extra_admins()


def add_admin(user_id, name: str = "", username: str = "", added_by=None) -> bool:
    """Returns False if already an admin (owner or already-added)."""
    uid = int(user_id)
    if is_admin(uid):
        return False
    data = load_extra_admins()
    data[str(uid)] = {
        "telegram_id": uid,
        "name": name or "",
        "username": username or "",
        "added_at": datetime.utcnow().isoformat(),
        "added_by": int(added_by) if added_by else None,
    }
    _save(data)
    return True


def remove_admin(user_id) -> bool:
    """Returns False if the id wasn't an extra admin (the owner can't be removed)."""
    uid = int(user_id)
    data = load_extra_admins()
    key = str(uid)
    if key not in data:
        return False
    del data[key]
    _save(data)
    return True


def all_admin_ids() -> list:
    """Owner first, then extra admins."""
    ids = []
    if ADMIN_ID:
        ids.append(int(ADMIN_ID))
    ids += extra_admin_ids()
    return ids
