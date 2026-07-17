#!/bin/bash

set -e

PROJECT="/root/alb70bot"
BACKUP="/root/backups"
LOG="/tmp/deploy_error.log"

rm -f "$LOG"

exec > >(tee -a "$LOG") 2>&1

echo "=================================="
echo "Deploy Started"
echo "=================================="

mkdir -p "$BACKUP"

echo ""
echo "Creating backup..."

FILE="$BACKUP/alb70bot-$(date +%Y%m%d-%H%M%S).tar.gz"

tar -czf "$FILE" "$PROJECT"

echo ""
echo "Backup created"

echo ""
echo "Deleting old backups..."

ls -1t "$BACKUP"/alb70bot-*.tar.gz | tail -n +11 | xargs -r rm -f

echo ""
echo "Current backups"

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
echo "Checking Python..."

python3 -m compileall .

echo ""
echo "Restarting bot..."

systemctl restart alb70bot

sleep 3

echo ""
echo "Checking service..."

systemctl --no-pager --full status alb70bot

echo ""
echo "=================================="
echo "Deploy Finished Successfully"
echo "=================================="
