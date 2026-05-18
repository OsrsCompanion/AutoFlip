#!/usr/bin/env bash
set -euo pipefail

# AUTOFLIP START MEMORY WATCHER v15

BASE_LOG_DIR="/mnt/nvme/autoflip-data/logs"
WATCHDOG_DIR="$BASE_LOG_DIR/watchdog"
PID_FILE="$WATCHDOG_DIR/memory_watcher.pid"
OUT_FILE="$WATCHDOG_DIR/memory_watcher.out.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHER="$SCRIPT_DIR/memory_watcher.sh"

mkdir -p "$WATCHDOG_DIR"

if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "memory_watcher already running pid=$OLD_PID"
    exit 0
  fi
fi

nohup "$WATCHER" >> "$OUT_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"
echo "memory_watcher v15 started pid=$PID"
echo "logs: $WATCHDOG_DIR/memory_watcher.log"
echo "deep dives: $BASE_LOG_DIR/memory_deep_dive/"
