# -*- coding: utf-8 -*-
"""
server_admin.monitor — 🔄 إعادة تشغيل البوت / ⛔ إيقاف الخدمة /
▶ تشغيل الخدمة / 📊 حالة البوت.
"""
from server_admin.utils import run_command, SERVICE_NAME


def act_restart():
    return run_command(["systemctl", "restart", SERVICE_NAME])


def act_stop():
    return run_command(["systemctl", "stop", SERVICE_NAME])


def act_start():
    return run_command(["systemctl", "start", SERVICE_NAME])


def act_status():
    # Informational query — "status" isn't pass/fail by itself (a cleanly
    # stopped service still returns a non-zero exit code), so we report it
    # as successful whenever the command itself ran without error.
    _, out = run_command(["systemctl", "status", SERVICE_NAME, "--no-pager"])
    return True, out
