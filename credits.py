# -*- coding: utf-8 -*-
import random
import string
import logging
from datetime import datetime
from storage import load_codes, save_codes, get_user, update_user

logger = logging.getLogger(__name__)

CREDIT_PER_CODE = 5   # fallback for legacy codes that lack a points field
HINT_COST = 5         # kept for reference


def normalize_code(v) -> dict:
    """Normalize a code entry — supports old bool format and new dict format."""
    if isinstance(v, dict):
        return v
    return {"points": CREDIT_PER_CODE, "used": bool(v), "used_by": None, "used_at": None}


def get_balance(user_id: int) -> int:
    user = get_user(user_id)
    return user.get("credits", 0) if user else 0


def add_credits(user_id: int, amount: int) -> int:
    user = get_user(user_id)
    new_balance = user.get("credits", 0) + amount
    update_user(user_id, credits=new_balance)
    return new_balance


def deduct_credits(user_id: int, amount: int) -> tuple[bool, int]:
    user = get_user(user_id)
    balance = user.get("credits", 0)
    if balance < amount:
        return False, balance
    new_balance = balance - amount
    update_user(user_id, credits=new_balance)
    return True, new_balance


def redeem_code(user_id: int, code: str) -> tuple[str, int, int]:
    """Returns ('ok', new_balance, pts) | ('used', balance, 0) | ('invalid', balance, 0)."""
    codes   = load_codes()
    code    = code.strip().upper()
    balance = get_balance(user_id)
    if code not in codes:
        return "invalid", balance, 0
    obj = normalize_code(codes[code])
    if obj["used"]:
        return "used", balance, 0
    obj["used"]    = True
    obj["used_by"] = user_id
    obj["used_at"] = datetime.utcnow().isoformat()
    codes[code]    = obj
    save_codes(codes)
    pts         = obj.get("points", CREDIT_PER_CODE)
    new_balance = add_credits(user_id, pts)
    try:
        from transactions import record as _rec
        user_obj = get_user(user_id)
        _rec(user_id, user_obj.get("full_name", "—"),
             "recharge_code", pts, balance, new_balance,
             f"شحن بكود {code}")
    except Exception as e:
        logger.warning("transaction record failed: %s", e)
    return "ok", new_balance, pts


def generate_codes(count: int, points: int = CREDIT_PER_CODE) -> list[str]:
    codes    = load_codes()
    existing = set(codes.keys())
    new_codes: list[str] = []
    chars    = string.ascii_uppercase + string.digits
    attempts = 0
    while len(new_codes) < count and attempts < count * 20:
        code = "".join(random.choices(chars, k=6))
        if code not in existing and code not in new_codes:
            new_codes.append(code)
        attempts += 1
    for code in new_codes:
        codes[code] = {"points": points, "used": False, "used_by": None, "used_at": None}
    save_codes(codes)
    return new_codes


def hint_mask(password: str, revealed: set) -> str:
    """Show revealed characters; hide the rest with '_'."""
    return " ".join(ch if i in revealed else "_" for i, ch in enumerate(password))
