# -*- coding: utf-8 -*-
"""
server_admin.rollback — restoring the bot to a previous state, whether via
the project's own scripts/rollback.sh (always the LATEST backup) or by
extracting any SPECIFIC backup archive chosen from the backup browser.
"""
import shutil

from server_admin.utils import run_command, ROLLBACK_SCRIPT, SERVICE_NAME, BOT_DIR
from server_admin.health import check_service_health


def act_rollback():
    """↩️ استرجاع آخر نسخة — runs scripts/rollback.sh (which itself stops
    the service, wipes the project folder, extracts the most recent backup,
    and restarts), then health-checks independently on top of that."""
    ok, out = run_command([ROLLBACK_SCRIPT], timeout=180)
    health_ok, health_out = check_service_health()
    combined = f"{out}\n\n── فحص الحالة بعد الاسترجاع (Health Check) ──\n{health_out}"
    if not health_ok:
        combined += "\n\n❌ تحذير: الخدمة لا تعمل بعد الاسترجاع!"
    return (ok and health_ok), combined


def act_restore_backup(filepath: str):
    """Restores ANY chosen backup (not just the latest) — mirrors
    scripts/rollback.sh's own recovery steps exactly, so a restore behaves
    identically regardless of which backup is picked: stop the service,
    wipe the current project folder (so files removed since that backup
    don't linger), extract the archive back over BOT_DIR, restart, then
    health-check."""
    stop_ok, stop_out = run_command(["systemctl", "stop", SERVICE_NAME])

    try:
        shutil.rmtree(BOT_DIR, ignore_errors=True)
        wipe_ok, wipe_out = True, f"تم حذف {BOT_DIR} الحالي بنجاح."
    except Exception as e:
        wipe_ok, wipe_out = False, f"⚠️ تعذر حذف {BOT_DIR} الحالي:\n{e}"

    extract_ok, extract_out = run_command(["tar", "-xzf", filepath, "-C", "/"], timeout=180)
    start_ok, start_out = run_command(["systemctl", "start", SERVICE_NAME])
    health_ok, health_out = check_service_health()

    combined = (
        f"── إيقاف الخدمة ──\n{stop_out}\n\n"
        f"── حذف النسخة الحالية ──\n{wipe_out}\n\n"
        f"── استخراج النسخة المختارة ──\n{extract_out}\n\n"
        f"── تشغيل الخدمة ──\n{start_out}\n\n"
        f"── فحص الحالة (Health Check) ──\n{health_out}"
    )
    if not health_ok:
        combined += "\n\n❌ تحذير: الخدمة لا تعمل بعد الاسترجاع!"
    return (wipe_ok and extract_ok and start_ok and health_ok), combined
