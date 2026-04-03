#!/usr/bin/env bash
# backup.sh — manually back up chat.db and memory/ for ai-chat
# Usage: bash backup.sh [destination-dir]
# Default destination: <project-root>/backups/<timestamp>/
# To run daily via cron (Mac):
#   crontab -e
#   0 3 * * * /Users/parasjain/ai-chat/backup.sh >> /tmp/ai-chat-backup.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP="$(date -u '+%Y%m%d_%H%M%S')"
DEST="${1:-${SCRIPT_DIR}/backups/${TIMESTAMP}}"

mkdir -p "${DEST}"

# Copy chat.db
if [ -f "${SCRIPT_DIR}/chat.db" ]; then
    cp "${SCRIPT_DIR}/chat.db" "${DEST}/chat.db"
    echo "[${TIMESTAMP}] Backed up chat.db → ${DEST}/chat.db"
else
    echo "[${TIMESTAMP}] WARNING: chat.db not found, skipping"
fi

# Copy memory/
if [ -d "${SCRIPT_DIR}/memory" ]; then
    cp -r "${SCRIPT_DIR}/memory" "${DEST}/memory"
    echo "[${TIMESTAMP}] Backed up memory/ → ${DEST}/memory/"
else
    echo "[${TIMESTAMP}] WARNING: memory/ not found, skipping"
fi

# Prune: keep only the 14 most recent backup directories
BACKUP_ROOT="${SCRIPT_DIR}/backups"
if [ -d "${BACKUP_ROOT}" ]; then
    # List dirs sorted by name (timestamps sort lexicographically), remove oldest
    mapfile -t ALL_BACKUPS < <(find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d | sort)
    TOTAL=${#ALL_BACKUPS[@]}
    KEEP=14
    if [ "${TOTAL}" -gt "${KEEP}" ]; then
        REMOVE=$(( TOTAL - KEEP ))
        for DIR in "${ALL_BACKUPS[@]:0:${REMOVE}}"; do
            rm -rf "${DIR}"
            echo "[${TIMESTAMP}] Pruned old backup: ${DIR}"
        done
    fi
fi

echo "[${TIMESTAMP}] Backup complete: ${DEST}"
