# -*- coding: utf-8 -*-
"""
server_admin.system — 🖥 معلومات وتشخيص.

Per the "simplify for phone use, merge similar things, fewer buttons"
redesign: this used to be 7 separate one-line buttons (Python version, Linux
version, disk, RAM, CPU, IP, time) PLUS 2 separate Git buttons. All of that
information is genuinely useful together as one quick diagnostic snapshot,
and none of it needs its own button — so it's now a single combined report,
reachable in exactly one tap from the main menu.
"""
import os

from server_admin.utils import run_command
from server_admin.github import act_git_status, act_git_log


def _cpu_load_text() -> str:
    try:
        load1, load5, load15 = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        return (
            f"متوسط الحمل (Load Average): {load1:.2f} / {load5:.2f} / {load15:.2f} "
            f"(1/5/15 دقيقة)\nعدد الأنوية: {cpu_count}"
        )
    except Exception as e:
        return f"⚠️ تعذر قراءة استهلاك المعالج:\n{e}"


def act_sysinfo():
    """One comprehensive snapshot: hostname/uptime/python/linux version, IP,
    disk, RAM, CPU load, and — since it's genuinely useful diagnostic info,
    not a daily action — the current git branch/commit and last 10 commits."""
    _, hostname_out = run_command(["hostname"], timeout=10)
    _, uptime_out = run_command(["uptime"], timeout=10)
    _, py_out = run_command(["python3", "--version"], timeout=10)
    _, linux_out = run_command(["uname", "-a"], timeout=10)
    _, ip_out = run_command(["hostname", "-I"], timeout=10)
    _, disk_out = run_command(["df", "-h"], timeout=15)
    _, ram_out = run_command(["free", "-h"], timeout=15)
    cpu_out = _cpu_load_text()
    _, git_status_out = act_git_status()
    _, git_log_out = act_git_log()

    blocks = [
        f"── الجهاز ──\nHostname: {hostname_out}\nUptime: {uptime_out}\nIP: {ip_out}",
        f"── الإصدارات ──\nPython: {py_out}\nLinux: {linux_out}",
        f"── الموارد ──\n{cpu_out}\n\n💽 مساحة القرص:\n{disk_out}\n\n💾 الرام:\n{ram_out}",
        f"── Git ──\n{git_status_out}\n\n📜 آخر 10 Commits:\n{git_log_out}",
    ]
    return True, "\n\n".join(blocks)
