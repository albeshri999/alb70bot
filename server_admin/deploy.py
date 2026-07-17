# -*- coding: utf-8 -*-
"""
server_admin.deploy — 🚀 نشر آخر تحديث.

Implements rules #14 and #15 of the production charter:
  #14 — every update automatically creates a backup FIRST.
  #15 — if the update fails (script error OR the service doesn't come back
        up healthy), it automatically rolls back and reports the outcome
        of that rollback too.
"""
from server_admin.utils import run_command, create_backup_archive, DEPLOY_SCRIPT
from server_admin.health import check_service_health
from server_admin.rollback import act_rollback


def act_deploy():
    # Rule #14 — automatic backup before touching anything.
    backup_ok, backup_out = create_backup_archive(prefix="auto")
    if not backup_ok:
        return False, (
            "❌ فشل إنشاء نسخة احتياطية تلقائية قبل النشر — تم إلغاء عملية "
            f"النشر لحماية النظام (لن يُنفَّذ Deploy بدون نسخة احتياطية ناجحة).\n\n{backup_out}"
        )

    deploy_ok, deploy_out = run_command([DEPLOY_SCRIPT])
    health_ok, health_out = check_service_health()

    report = (
        f"── نسخة احتياطية تلقائية قبل النشر ──\n{backup_out}\n\n"
        f"── تنفيذ النشر ──\n{deploy_out}\n\n"
        f"── فحص الحالة بعد النشر (Health Check) ──\n{health_out}"
    )

    if deploy_ok and health_ok:
        return True, report

    # Rule #15 — the update failed (or the service isn't healthy) → auto-rollback.
    report += "\n\n⚠️ فشل النشر أو الخدمة لا تعمل بعده — سيتم التراجع (Rollback) تلقائياً..."
    rollback_ok, rollback_out = act_rollback()
    report += f"\n\n── Rollback تلقائي ──\n{rollback_out}"
    if rollback_ok:
        report += "\n\n✅ تم التراجع التلقائي بنجاح — الخدمة تعمل الآن بالنسخة السابقة."
    else:
        report += "\n\n❌ فشل التراجع التلقائي أيضاً! يتطلب تدخلاً يدوياً فورياً."
    return False, report
