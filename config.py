import os

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_TELEGRAM_ID", "0"))

MAX_PASSWORD_ATTEMPTS = 3
LOCKOUT_DURATION_HOURS = 3

DAYS_FILE         = "data/days.json"
USERS_FILE        = "data/users.json"
CODES_FILE        = "data/recharge_codes.json"
CONFIG_FILE       = "data/config.json"
CREDIT_LOG_FILE   = "data/credit_log.json"
TRANSACTIONS_FILE = "data/transactions.json"
ADMIN_LOG_FILE    = "data/admin_log.json"
# test
