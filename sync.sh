#!/bin/bash
# sync.sh — sync local Dropbox changes to Mac Mini and restart the server
#
# Usage:
#   ./sync.sh            — sync everything and restart
#   ./sync.sh --no-restart  — sync only, don't restart
#
# Run from Windows via Git Bash or WSL:
#   bash /c/dropbox/ai-chat/sync.sh

set -e

MAC="parasjain@192.168.0.130"
REMOTE="/Users/parasjain/ai-chat"
LOCAL="$(cd "$(dirname "$0")" && pwd)"

RESTART=true
if [[ "${1:-}" == "--no-restart" ]]; then
  RESTART=false
fi

echo "==> Syncing Python modules..."
scp \
  "$LOCAL/server.py" \
  "$LOCAL/db.py" \
  "$LOCAL/models.py" \
  "$LOCAL/orchestration.py" \
  "$LOCAL/files_io.py" \
  "$LOCAL/skills_mod.py" \
  "$MAC:$REMOTE/"

echo "==> Syncing static files..."
scp "$LOCAL/static/index.html" "$LOCAL/static/app.js" "$LOCAL/static/app.css" "$MAC:$REMOTE/static/"

echo "==> Syncing skills..."
scp "$LOCAL/skills/"*.md "$MAC:$REMOTE/skills/"

echo "==> Syncing requirements..."
scp "$LOCAL/requirements.txt" "$MAC:$REMOTE/requirements.txt"

if $RESTART; then
  echo "==> Restarting server..."
  ssh "$MAC" "
    pkill -f 'uvicorn server:app' 2>/dev/null || true
    sleep 1
    cd $REMOTE
    nohup .venv/bin/uvicorn server:app --host 0.0.0.0 --port 8080 > /tmp/ai-chat.log 2>&1 &
    sleep 2
    curl -s http://localhost:8080/status
    echo ''
  "
  echo "==> Done. Server is up."
else
  echo "==> Sync complete (no restart)."
fi
