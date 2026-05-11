#!/bin/bash
set -e

echo "==> Syncing code to /opt/helpfuldjinn..."
sudo rsync -a --exclude='.claude' --exclude='venv' --exclude='instance' --exclude='.env' --exclude='__pycache__'     /home/ubuntu/HelpfulDjinn/ /opt/helpfuldjinn/
sudo chown -R ubuntu:ubuntu /opt/helpfuldjinn

echo "==> Restarting HelpfulDjinn (web + scheduler)..."
sudo systemctl restart helpfuldjinn helpfuldjinn-scheduler

echo "==> Checking service status..."
sudo systemctl is-active helpfuldjinn           && echo "Web:       RUNNING" || echo "Web:       FAILED"
sudo systemctl is-active helpfuldjinn-scheduler && echo "Scheduler: RUNNING" || echo "Scheduler: FAILED"

echo "==> Waiting for app to be ready..."
sleep 2

echo "==> Recent web logs (last 5 lines):"
sudo journalctl -u helpfuldjinn -n 5 --no-pager

echo ""
echo "==> Recent scheduler logs (last 5 lines):"
sudo journalctl -u helpfuldjinn-scheduler -n 5 --no-pager

echo ""
echo "==> Done."
echo "    Follow web logs:       sudo journalctl -u helpfuldjinn -f"
echo "    Follow scheduler logs: sudo journalctl -u helpfuldjinn-scheduler -f"
