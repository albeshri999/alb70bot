# -*- coding: utf-8 -*-
"""
server_admin — a fully independent, ADMIN_ID-only server management panel,
completely separate from the competition system (rules #2/#20).

main.py only ever needs:
    from server_admin import build_server_admin_handler

...which keeps working unchanged whether server_admin is a single module or
(as now) a full package — this __init__.py is the only reason why.
"""
from server_admin.menu import build_server_admin_handler

__all__ = ["build_server_admin_handler"]
