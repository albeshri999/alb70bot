# -*- coding: utf-8 -*-
"""
server_admin.system — 🖥 معلومات السيرفر (شامل) وكل بند مستقل: إصدار Python،
إصدار Linux، مساحة القرص، استهلاك الرام، استهلاك المعالج، عنوان IP، وقت السيرفر.
"""
import os

from server_admin.utils import run_command


def act_py_version():
    return run_command(["python3", "--version"], timeout=15)


def act_linux_version():
    return run_command(["uname", "-a"], timeout=15)


def act_disk():
    return run_command(["df", "-h"], timeout=15)


def act_ram():
    return run_command(["free", "-h"], timeout=15)


def act_cpu():
    try:
        load1, load5, load15 = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        text = (
            f"متوسط الحمل (Load Average):\n"
            f"  1 دقيقة: {load1:.2f}\n"
            f"  5 دقائق: {load5:.2f}\n"
            f"  15 دقيقة: {load15:.2f}\n\n"
            f"عدد الأنوية: {cpu_count}"
        )
        return True, text
    except Exception as e:
        return False, f"⚠️ تعذر قراءة استهلاك المعالج:\n{e}"


def act_ip():
    return run_command(["hostname", "-I"], timeout=15)


def act_time():
    return run_command(["date"], timeout=15)


def act_sysinfo():
    checks = [
        ("hostname", ["hostname"]),
        ("uptime", ["uptime"]),
        ("free -h", ["free", "-h"]),
        ("df -h", ["df", "-h"]),
        ("python3 --version", ["python3", "--version"]),
    ]
    blocks = []
    for label, cmd in checks:
        _, out = run_command(cmd, timeout=15)
        blocks.append(f"── {label} ──\n{out}")
    return True, "\n\n".join(blocks)
