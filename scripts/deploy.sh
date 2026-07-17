#!/bin/bash

set -e

PROJECT="/root/alb70bot"
BACKUP="/root/backups"

echo "=============================="
echo "Create Backup"
echo "=============================="

mkdir -p "$BACKUP"

FILE="$BACKUP/alb70bot-$(date +%Y%m%d-%H%M%S).tar.gz"

tar -czf "$FILE" "$PROJECT"

echo "Backup created:"
echo "$FILE"

echo ""

echo "Delete old backups..."

ls -1t "$BACKUP"/alb70bot-*.tar.gz | tail -n +11 | xargs -r rm -f

echo ""

echo "Current backups"

ls -lh "$BACKUP"

echo ""

echo "=============================="
echo "Enter Project"
echo "=============================="

cd "$PROJECT"

echo ""

echo "Git Pull"

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

python3 -m compileall .

echo ""

systemctl restart alb70bot

sleep 3

systemctl --no-pager --full status alb70bot

echo ""

echo "Deploy Finished Successfully"
