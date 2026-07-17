# -*- coding: utf-8 -*-
"""
server_admin.update — 📦 تحديث المكتبات / 🧹 تنظيف Cache / 🧪 فحص الكود.

pip updates can just as easily break a running bot as a code deploy can, so
act_pip_update() follows the exact same rule #14/#15 safety pattern as
deploy.py: automatic backup first, restart + health-check after, and an
automatic rollback if the service doesn't come back up healthy.
"""
import os
import py_compile
import shutil

from server_admin.utils import run_command, create_backup_archive, BOT_DIR, SERVICE_NAME
from server_admin.health import check_service_health
from server_admin.rollback import act_rollback


def act_pip_update():
    # Rule #14 — automatic backup before touching anything.
    backup_ok, backup_out, _backup_path = create_backup_archive(prefix="auto")
    if not backup_ok:
        return False, (
            "❌ فشل إنشاء نسخة احتياطية تلقائية قبل التحديث — تم إلغاء تحديث "
            f"المكتبات لحماية النظام.\n\n{backup_out}"
        )

    # Needs an actual shell (source + &&), so this is the one action that
    # uses shell=True — still via subprocess.run(), never os.system().
    cmd = f"cd {BOT_DIR} && source venv/bin/activate && pip install -r requirements.txt"
    pip_ok, pip_out = run_command(cmd, timeout=300, shell=True)

    # Restart so the new libraries actually take effect, then health-check —
    # otherwise "did the update work?" would be unanswerable.
    restart_ok, restart_out = run_command(["systemctl", "restart", SERVICE_NAME])
    health_ok, health_out = check_service_health()

    report = (
        f"── نسخة احتياطية تلقائية قبل التحديث ──\n{backup_out}\n\n"
        f"── تثبيت المتطلبات (pip install) ──\n{pip_out}\n\n"
        f"── إعادة تشغيل الخدمة لتفعيل المكتبات الجديدة ──\n{restart_out}\n\n"
        f"── فحص الحالة (Health Check) ──\n{health_out}"
    )

    if pip_ok and restart_ok and health_ok:
        return True, report

    # Rule #15 — the update failed (or the service isn't healthy) → auto-rollback.
    report += "\n\n⚠️ فشل تحديث المكتبات أو الخدمة لا تعمل بعده — سيتم التراجع (Rollback) تلقائياً..."
    rollback_ok, rollback_out = act_rollback()
    report += f"\n\n── Rollback تلقائي ──\n{rollback_out}"
    if rollback_ok:
        report += "\n\n✅ تم التراجع التلقائي بنجاح — الخدمة تعمل الآن بالنسخة السابقة."
    else:
        report += "\n\n❌ فشل التراجع التلقائي أيضاً! يتطلب تدخلاً يدوياً فورياً."
    return False, report


def act_cache_clean():
    """🧹 تنظيف Cache — removes every __pycache__ dir and .pyc file under
    BOT_DIR. Implemented directly in Python (not a shell find/exec
    pipeline) for safety and predictability."""
    removed_dirs, removed_files, errors = 0, 0, []
    for root, dirs, files in os.walk(BOT_DIR):
        if "__pycache__" in dirs:
            path = os.path.join(root, "__pycache__")
            try:
                shutil.rmtree(path)
                removed_dirs += 1
                dirs.remove("__pycache__")
            except Exception as e:
                errors.append(str(e))
        for f in files:
            if f.endswith(".pyc"):
                try:
                    os.remove(os.path.join(root, f))
                    removed_files += 1
                except Exception as e:
                    errors.append(str(e))
    msg = f"🧹 تم حذف {removed_dirs} مجلد __pycache__ و {removed_files} ملف .pyc."
    if errors:
        msg += "\n\n⚠️ أخطاء:\n" + "\n".join(errors[:20])
    return (len(errors) == 0), msg


def act_codecheck():
    """🧪 فحص الكود — compiles every .py file under BOT_DIR to catch syntax
    errors, without executing any of them."""
    errors, checked = [], 0
    for root, dirs, files in os.walk(BOT_DIR):
        dirs[:] = [d for d in dirs if d not in (".git", "venv", "__pycache__")]
        for f in files:
            if f.endswith(".py"):
                checked += 1
                path = os.path.join(root, f)
                try:
                    py_compile.compile(path, doraise=True)
                except py_compile.PyCompileError as e:
                    errors.append(f"{path}:\n{e}")
                except Exception as e:
                    errors.append(f"{path}: {e}")
    if errors:
        return False, f"🧪 تم فحص {checked} ملف — وُجدت {len(errors)} مشكلة:\n\n" + "\n\n".join(errors)
    return True, f"✅ تم فحص {checked} ملف Python — لا توجد أخطاء صياغة."
