# -*- coding: utf-8 -*-
"""
server_admin.deploy — 🚀 نشر تحديث.

/root/alb70bot/scripts/deploy.sh already does its own backup (before
touching anything), git pull, pip install, compileall, restart, and an
internal health check (`systemctl is-active --quiet`) that makes the whole
script exit non-zero on failure — so this module does NOT create a second,
redundant backup (that would just waste time/disk and produce two
differently-named backups for one deploy). What this module adds on top:
  - its OWN health check right after, as a second independent confirmation,
  - automatic rollback if the script failed OR the service isn't healthy,
    with the rollback's own outcome included in the report (production
    charter rule: "any failed update must roll back automatically").
"""
from server_admin.utils import run_command, DEPLOY_SCRIPT
from server_admin.health import check_service_health
from server_admin.rollback import act_rollback

# deploy.sh runs git pull + pip install + compileall + restart — needs more
# room than the default 120s.
DEPLOY_TIMEOUT = 300


def act_deploy():
    deploy_ok, deploy_out = run_command([DEPLOY_SCRIPT], timeout=DEPLOY_TIMEOUT)
    health_ok, health_out = check_service_health()

    report = (
        f"── تنفيذ النشر (deploy.sh) ──\n{deploy_out}\n\n"
        f"── فحص الحالة بعد النشر (Health Check) ──\n{health_out}"
    )

    if deploy_ok and health_ok:
        return True, report

    # The update failed (or the service isn't healthy) → auto-rollback.
    report += "\n\n⚠️ فشل النشر أو الخدمة لا تعمل بعده — سيتم التراجع (Rollback) تلقائياً..."
    rollback_ok, rollback_out = act_rollback()
    report += f"\n\n── Rollback تلقائي ──\n{rollback_out}"
    if rollback_ok:
        report += "\n\n✅ تم التراجع التلقائي بنجاح — الخدمة تعمل الآن بالنسخة السابقة."
    else:
        report += "\n\n❌ فشل التراجع التلقائي أيضاً! يتطلب تدخلاً يدوياً فورياً."
    return False, report
