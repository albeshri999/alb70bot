# -*- coding: utf-8 -*-
"""
server_admin.github — read-only Git diagnostics (branch, last commit, recent
history). Per the project's philosophy that GitHub is just a version
repository — not a daily-use tool — there is deliberately NO "git pull"
action here: scripts/deploy.sh already pulls the latest code as part of a
proper deploy (backup → pull → install → restart → health-check). Exposing
a bare "git pull" separately would create a second, less-safe code-update
path with no backup/restart/health-check around it, which is exactly the
kind of redundant/confusing control the panel should avoid.
"""
from server_admin.utils import run_command, BOT_DIR


def act_git_status():
    ok, out = run_command(["git", "status"], cwd=BOT_DIR)
    _, branch_out = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], timeout=15, cwd=BOT_DIR)
    _, hash_out = run_command(["git", "rev-parse", "HEAD"], timeout=15, cwd=BOT_DIR)
    branch = branch_out.splitlines()[0] if branch_out else "—"
    commit_hash = hash_out.splitlines()[0] if hash_out else "—"
    header = f"🌿 الفرع الحالي: {branch}\n🔖 آخر Commit: {commit_hash}\n\n"
    return ok, header + out


def act_git_log():
    return run_command(["git", "log", "-10", "--oneline", "--decorate"], cwd=BOT_DIR)
