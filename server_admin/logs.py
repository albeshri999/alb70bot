# -*- coding: utf-8 -*-
"""
server_admin.logs — 📜 آخر 50 سجل / ❌ آخر الأخطاء / 📄 تحميل آخر سجل.
"""
from server_admin.utils import run_command, timestamp, SERVICE_NAME


def act_logs():
    _, out = run_command(["journalctl", "-u", SERVICE_NAME, "-n", "50", "--no-pager"])
    return True, out


def act_errors():
    _, out = run_command(["journalctl", "-u", SERVICE_NAME, "-p", "err", "-n", "50", "--no-pager"])
    return True, out


def build_latest_log():
    """📄 تحميل آخر سجل — dumps the most recent 500 service-log lines to a
    temp .txt file for download (return signature matches backup.py's
    download builders: (success, message, filepath_or_None))."""
    ok, out = run_command(["journalctl", "-u", SERVICE_NAME, "-n", "500", "--no-pager"])
    if not ok:
        return False, out, None
    tmp_path = f"/tmp/latest_log-{timestamp()}.txt"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(out)
        return True, "تم تجهيز آخر سجل بنجاح.", tmp_path
    except Exception as e:
        return False, f"⚠️ تعذر كتابة ملف السجل:\n{e}", None
