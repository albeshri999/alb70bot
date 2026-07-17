#!/bin/bash

set -Eeuo pipefail

PROJECT="/root/alb70bot"
BACKUP="/root/backups"
SERVICE="alb70bot"
ERROR_LOG="/tmp/deploy_error.log"

mkdir -p "$BACKUP"

rm -f "$ERROR_LOG"

log_error() {
{
echo "=================================="
echo "DEPLOY FAILED"
echo "DATE: $(date)"
echo "=================================="
echo

echo "Service Status"
systemctl --no-pager --full status "$SERVICE" || true

echo
echo "----------------------------------"
echo "Last 50 Service Logs"
echo "----------------------------------"

journalctl -u "$SERVICE" -n 50 --no-pager || true

echo
echo "----------------------------------"
echo "Disk Usage"
echo "----------------------------------"

df -h

echo
echo "----------------------------------"
echo "Memory"
echo "----------------------------------"

free -h

} > "$ERROR_LOG"
}

trap 'log_error' ERR

echo "=================================="
echo "Deploy Started"
echo "=================================="

mkdir -p "$BACKUP"

BACKUP_FILE="$BACKUP/alb70bot-$(date +%Y%m%d-%H%M%S).tar.gz"

tar -czf "$BACKUP_FILE" "$PROJECT"

ls -1t "$BACKUP"/alb70bot-*.tar.gz | tail -n +11 | xargs -r rm -f

cd "$PROJECT"

git pull origin main

if [ -f venv/bin/activate ]; then
    source venv/bin/activate
fi

if [ -f requirements.txt ]; then
    pip install -r requirements.txt
fi

python3 -m compileall .

systemctl restart "$SERVICE"

sleep 3

systemctl is-active --quiet "$SERVICE"

echo "=================================="
echo "Deploy Success"
echo "=================================="
