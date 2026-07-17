#!/bin/bash

set -Eeuo pipefail

PROJECT="/root/alb70bot"
BACKUP="/root/backups"
SERVICE="alb70bot"

mkdir -p "$BACKUP"

on_error() {
    echo ""
    echo "=============================="
    echo "DEPLOY FAILED"
    echo "=============================="

    echo ""
    echo "Service status:"
    systemctl --no-pager --full status "$SERVICE" || true

    echo ""
    echo "Last 50 log lines:"
    journalctl -u "$SERVICE" -n 50 --no-pager || true

    exit 1
}

trap on_error ERR

echo "=============================="
echo "Deploy Started"
echo "=============================="

echo ""
echo "Creating backup..."

BACKUP_FILE="$BACKUP/alb70bot-$(date +%Y%m%d-%H%M%S).tar.gz"

tar -czf "$BACKUP_FILE" "$PROJECT"

echo "Backup:"
echo "$BACKUP_FILE"

echo ""
echo "Removing old backups..."

ls -1t "$BACKUP"/alb70bot-*.tar.gz | tail -n +11 | xargs -r rm -f

echo ""
echo "Current backups:"

ls -lh "$BACKUP"

echo ""
echo "Entering project..."

cd "$PROJECT"

echo ""
echo "Git Pull..."

git pull origin main

echo ""

if [ -f venv/bin/activate ]; then
    source venv/bin/activate
fi

echo ""

if [ -f requirements.txt ]; then
    pip install -r requirements.txt
fi

echo ""
echo "Python compile..."

python3 -m compileall .

echo ""
echo "Restarting service..."

systemctl restart "$SERVICE"

sleep 3

echo ""
echo "Checking service..."

systemctl is-active --quiet "$SERVICE"

systemctl --no-pager --full status "$SERVICE"

echo ""
echo "=============================="
echo "DEPLOY SUCCESS"
echo "=============================="
