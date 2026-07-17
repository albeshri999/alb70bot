# -*- coding: utf-8 -*-
"""
server_admin.rollback — restoring the bot to a previous state, whether via
the project's own /root/rollback.sh (latest version) or by extracting any
specific backup archive chosen from the backup browser.
"""
from server_admin.utils import run_command, ROLLBACK_SCRIPT, SERVICE_NAME
from server_admin.health import check_service_health


def act_rollback():
    """↩️ استرجاع آخر نسخة — runs /root/rollback.sh, then health-checks."""
    ok, out = run_command([ROLLBACK_SCRIPT])
    health_ok, health_out = check_service_health()
    combined = f"{out}\n\n── فحص الحالة بعد الاسترجاع (Health Check) ──\n{health_out}"
    if not health_ok:
        combined += "\n\n❌ تحذير: الخدمة لا تعمل بعد الاسترجاع!"
    return (ok and health_ok), combined


def act_restore_backup(filepath: str):
    """Restores ANY chosen backup (not just the latest) — stops the service,
    extracts the archive back over BOT_DIR, restarts, then health-checks."""
    stop_ok, stop_out = run_command(["systemctl", "stop", SERVICE_NAME])
    extract_ok, extract_out = run_command(["tar", "-xzf", filepath, "-C", "/"])
    start_ok, start_out = run_command(["systemctl", "start", SERVICE_NAME])
    health_ok, health_out = check_service_health()
    combined = (
        f"── إيقاف الخدمة ──\n{stop_out}\n\n"
        f"── استخراج النسخة ──\n{extract_out}\n\n"
        f"── تشغيل الخدمة ──\n{start_out}\n\n"
        f"── فحص الحالة (Health Check) ──\n{health_out}"
    )
    if not health_ok:
        combined += "\n\n❌ تحذير: الخدمة لا تعمل بعد الاسترجاع!"
    return (extract_ok and start_ok and health_ok), combined
