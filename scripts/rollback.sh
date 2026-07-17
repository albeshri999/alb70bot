#!/bin/bash

set -Eeuo pipefail

PROJECT="/root/alb70bot"
BACKUP="/root/backups"
SERVICE="alb70bot"

echo "=============================="
echo "Rollback Started"
echo "=============================="

LAST_BACKUP=$(ls -1t "$BACKUP"/alb70bot-*.tar.gz | head -n 1)

if [ -z "$LAST_BACKUP" ]; then
    echo "No backup found."
    exit 1
fi

echo ""
echo "Using backup:"
echo "$LAST_BACKUP"

echo ""
echo "Stopping service..."

systemctl stop "$SERVICE"

echo ""
echo "Removing current project..."

rm -rf "$PROJECT"

echo ""
echo "Restoring backup..."

tar -xzf "$LAST_BACKUP" -C /

echo ""
echo "Starting service..."

systemctl start "$SERVICE"

sleep 3

echo ""
echo "Checking service..."

systemctl --no-pager --full status "$SERVICE"

echo ""
echo "=============================="
echo "Rollback Finished"
echo "=============================="
