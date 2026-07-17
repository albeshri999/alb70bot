# -*- coding: utf-8 -*-
"""
server_admin.utils — shared constants and low-level, dependency-free helpers.

Every other module in this package imports from here. This module itself
imports nothing from the rest of server_admin (no cycles), and nothing at
all from the competition system — this whole package is fully independent
of it, per the project's rule #2/#20.
"""
import os
import subprocess
from datetime import datetime

from telegram import Update
from telegram.constants import ParseMode

from config import ADMIN_ID

# ── Server-specific paths/names (adjust here only, nowhere else) ────────────
BOT_DIR         = "/root/alb70bot"
SERVICE_NAME    = "alb70bot"
BACKUPS_DIR     = "/root/backups"
DEPLOY_SCRIPT   = "/root/deploy.sh"
ROLLBACK_SCRIPT = "/root/rollback.sh"
KEEP_BACKUPS    = 10
COMMAND_TIMEOUT = 120  # every subprocess call uses this — no exceptions

MAX_MESSAGE_LEN = 3500
NO_PERMISSION_TEXT = "🚫 ليس لديك صلاحية."

# ── Conversation states — defined ONCE here (the dependency-free base
# module every other module already imports) so menu.py and backup.py can
# share the exact same state values without any circular import between
# them. menu.py builds the actual ConversationHandler; backup.py's handlers
# just need to return the correct state constant when they finish.
(SRV_MENU, SRV_CONFIRM, SRV_BACKUP_LIST, SRV_BACKUP_ITEM, SRV_BACKUP_CONFIRM) = range(5)


def is_owner(user_id) -> bool:
    """Rule #2: only ADMIN_ID may ever use this panel — checked at every
    single entry point in every module, never just once at the top."""
    try:
        return int(user_id) == int(ADMIN_ID)
    except Exception:
        return False


async def reply(update: Update, text: str, keyboard=None):
    """Edits the triggering callback's message in place if possible, falling
    back to a plain reply — used for all menu/navigation screens."""
    import logging
    logger = logging.getLogger(__name__)
    kw = {"text": text, "parse_mode": ParseMode.MARKDOWN}
    if keyboard:
        kw["reply_markup"] = keyboard
    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(**kw)
        except Exception as e1:
            logger.warning("server_admin.utils.reply edit_text failed: %s", e1)
            try:
                await update.callback_query.message.reply_text(**kw)
            except Exception as e2:
                logger.error("server_admin.utils.reply reply_text also failed: %s", e2)
    else:
        await update.message.reply_text(**kw)


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def fmt_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f}TB"


def run_command(cmd, timeout: int = COMMAND_TIMEOUT, shell: bool = False, cwd: str = None):
    """Rule #9: every Linux command goes through subprocess.run() — never
    os.system() — always with capture_output=True, text=True, an explicit
    timeout, and check=False. Returns (success: bool, report: str), never
    raises."""
    try:
        result = subprocess.run(
            cmd, shell=shell, cwd=cwd,
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        parts = []
        if result.stdout:
            parts.append(result.stdout.strip())
        if result.stderr:
            parts.append("── stderr ──\n" + result.stderr.strip())
        parts.append(f"(exit code: {result.returncode})")
        return result.returncode == 0, "\n\n".join(p for p in parts if p)
    except subprocess.TimeoutExpired:
        return False, f"⚠️ انتهى الوقت المحدد لتنفيذ الأمر (Timeout بعد {timeout} ثانية)."
    except FileNotFoundError as e:
        return False, f"⚠️ لم يتم العثور على الأمر أو الملف المطلوب:\n{e}"
    except Exception as e:
        return False, f"⚠️ حدث خطأ غير متوقع أثناء التنفيذ:\n{e}"


def list_backup_files() -> list:
    """All backup archives (both manual- and auto- prefixed), most recent
    first — the single source of truth used by backup.py and rollback.py."""
    if not os.path.isdir(BACKUPS_DIR):
        return []
    files = [os.path.join(BACKUPS_DIR, f) for f in os.listdir(BACKUPS_DIR)
             if f.endswith((".tar.gz", ".tgz"))]
    files = [f for f in files if os.path.isfile(f)]
    files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    return files


def create_backup_archive(prefix: str = "manual"):
    """Creates one backup archive of BOT_DIR under BACKUPS_DIR. `prefix`
    distinguishes manually-requested backups ('manual-...') from the
    automatic ones rule #14 requires before every deploy/update
    ('auto-...') — both show up together in the backup browser."""
    try:
        os.makedirs(BACKUPS_DIR, exist_ok=True)
    except Exception as e:
        return False, f"⚠️ تعذر إنشاء مجلد النسخ الاحتياطية:\n{e}"
    archive_path = os.path.join(BACKUPS_DIR, f"{prefix}-{timestamp()}.tar.gz")
    return run_command(["tar", "-czf", archive_path, BOT_DIR])
