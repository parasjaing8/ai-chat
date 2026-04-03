#!/bin/bash
# start.sh — start the ai-chat server
#
# PREFERRED: use launchd for auto-restart on crash (macOS)
#   cp com.ai-chat.server.plist ~/Library/LaunchAgents/
#   launchctl load -w ~/Library/LaunchAgents/com.ai-chat.server.plist
#
# This script is kept as a manual / dev fallback.

set -e
cd /Users/parasjain/ai-chat

# Kill any existing instance first
EXISTING=$(pgrep -f "uvicorn server:app" 2>/dev/null || true)
if [ -n "$EXISTING" ]; then
  echo "Stopping existing server (PID $EXISTING)..."
  kill "$EXISTING" 2>/dev/null || true
  sleep 1
fi

exec /Users/parasjain/ai-chat/.venv/bin/python -m uvicorn server:app \
  --host 0.0.0.0 --port 8080
