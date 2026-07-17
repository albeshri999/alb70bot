# CLAUDE.md

# Telegram Competition Bot

## Project Status

This project is considered **Production Ready**.

The competition system is stable.

Do NOT redesign it.

Do NOT refactor it.

Do NOT replace it.

All future development must focus only on the Server Management module.

---

# Primary Goal

The project has two independent systems:

1. Competition System (Stable)
2. Server Management System (Under Development)

Only the Server Management System may evolve.

---

# Competition System

The following parts are frozen.

Never redesign them.

Never move them.

Never refactor them unless explicitly requested.

This includes:

- Participants
- Tests
- Questions
- Competition Days
- Password Words
- Scores
- Balances
- Rankings
- Statistics
- User Menus
- Admin Menus related to competition
- CallbackData
- ConversationHandlers
- Storage Logic
- JSON Structure
- Competition Database

If a requested feature requires modifying these components,
stop and explain why.

---

# Server Management

Only this part may receive new features.

All server-related code must live inside

server_admin/

Never place server logic inside

main.py

handlers.py

admin.py

except for imports and handler registration.

---

# Folder Structure

Use this structure:

server_admin/

    __init__.py

    menu.py

    deploy.py

    update.py

    rollback.py

    backup.py

    monitor.py

    logs.py

    github.py

    system.py

    health.py

    notifications.py

    utils.py

Every feature must have its own module.

Never create huge files.

---

# Code Style

Follow:

- SOLID
- Clean Architecture
- DRY
- KISS

Avoid duplicated code.

Avoid giant functions.

Prefer small reusable functions.

---

# Forbidden

Never use:

os.system()

Always use

subprocess.run()

with

capture_output=True

text=True

timeout=

check=False

---

# User Interface

Server Management must be independent.

Root menu:

🖥 Server Management

Submenus:

🚀 Updates

📊 Monitoring

💾 Backups

📜 Logs

🖥 System

⚙ Settings

---

# Long Operations

Always send

⌛ Working...

before execution.

Then

✅ Success

or

❌ Failed

after completion.

---

# Confirmation

Require confirmation before:

Deploy

Rollback

Restart

Stop

Delete

Cleanup

Install Updates

---

# Long Output

If output > 3500 chars

Generate TXT file

Send as Telegram Document

Do not send huge messages.

---

# Logging

Every operation must be logged.

Log file:

server_admin.log

Store:

Timestamp

Admin Name

Telegram ID

Operation

Execution Time

Exit Code

Result

---

# Backup

Every update

Every deploy

Every rollback

must automatically create a backup.

---

# Rollback

If deployment fails

Automatically restore the latest backup.

Never leave the bot offline.

---

# Safety

Allow only ADMIN_ID.

Reject everyone else.

Never execute arbitrary shell commands received from Telegram.

Whitelist only known operations.

---

# Concurrency

Never execute two server operations simultaneously.

Use a global execution lock.

---

# Health Check

After:

Deploy

Rollback

Restart

Run:

systemctl is-active alb70bot

If inactive

Send failure notification.

---

# Notifications

Always notify admin after:

Deploy

Rollback

Backup

Restore

Restart

Failure

---

# Git

GitHub is only a code repository.

Telegram is the primary administration interface.

---

# Future Vision

The owner should manage the entire server using only:

1. Claude

2. Telegram

No VS Code.

No SSH.

No GitHub UI.

No terminal.

No desktop tools.

All server administration should eventually be possible from Telegram.

---

# Before Every Change

Analyze:

Will this modify the Competition System?

If YES

STOP.

Do not implement.

Explain why.

If NO

Proceed.

---

# After Every Change

Return a report:

Modified files

New files

Deleted files

Reason

Architecture impact

Restart required?

Requirements update?

Potential risks?
