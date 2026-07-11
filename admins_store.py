# -*- coding: utf-8 -*-
"""
Multi-admin support — fully additive on top of the existing single-owner
ADMIN_ID system (config.py), and independent of every other storage file.

Model:
- The "owner" is whoever ADMIN_ID (env var TELEGRAM_ADMIN_ID) points to.
  Only the owner can add/remove other admins — this keeps one person always
  in ultimate control and prevents admins from locking each other out.
- Extra admins are stored in their own JSON file (data/extra_admins.json) as
  a plain list of Telegram user IDs. They get full access to everything the
  admin panel offers (word competition management, quizzes, credits, etc.)
  exactly like the owner — nothing about admin CAPABILITIES changes, only
  who is recognized as an admin.
"""
import json
import os

from config import ADMIN_ID

EXTRA_ADMINS_FILE = "data/extra_admins.json"


def _load() -> list:
    os.makedirs(os.path.dirname(EXTRA_ADMINS_FILE), exist_ok=True)
    if not os.path.exists(EXTRA_ADMINS_FILE):
        return []
    with open(EXTRA_ADMINS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(ids: list) -> None:
    os.makedirs(os.path.dirname(EXTRA_ADMINS_FILE), exist_ok=True)
    with open(EXTRA_ADMINS_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f, indent=2, ensure_ascii=False)


def load_extra_admins() -> list:
    """Returns the list of extra-admin Telegram IDs (ints), owner excluded."""
    return [int(x) for x in _load()]


def is_owner(user_id: int) -> bool:
    return bool(ADMIN_ID) and int(user_id) == int(ADMIN_ID)


def is_admin(user_id: int) -> bool:
    """True for the owner (ADMIN_ID) OR anyone added via /addadmin."""
    uid = int(user_id)
    if is_owner(uid):
        return True
    return uid in load_extra_admins()


def add_admin(user_id: int) -> bool:
    """Returns False if already an admin (owner or already-added)."""
    uid = int(user_id)
    if is_admin(uid):
        return False
    ids = load_extra_admins()
    ids.append(uid)
    _save(ids)
    return True


def remove_admin(user_id: int) -> bool:
    """Returns False if the id wasn't an extra admin (owner can't be removed)."""
    uid = int(user_id)
    ids = load_extra_admins()
    if uid not in ids:
        return False
    ids = [i for i in ids if i != uid]
    _save(ids)
    return True


def all_admin_ids() -> list:
    """Owner first, then extra admins."""
    ids = []
    if ADMIN_ID:
        ids.append(int(ADMIN_ID))
    ids += load_extra_admins()
    return ids
