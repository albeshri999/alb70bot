# -*- coding: utf-8 -*-
"""
server_admin.health — the single health-check used after every deploy,
rollback, pip update, or per-backup restore (rule #15's "did it actually
come back up?" gate).
"""
from server_admin.utils import run_command, SERVICE_NAME


def check_service_health(service: str = SERVICE_NAME):
    """systemctl is-active <service> — exit code 0 only when truly active.
    Returns (healthy: bool, report: str)."""
    return run_command(["systemctl", "is-active", service], timeout=15)
