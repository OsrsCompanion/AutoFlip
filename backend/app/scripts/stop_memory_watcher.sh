#!/usr/bin/env bash
set -euo pipefail

# AUTOFLIP STOP MEMORY WATCHER v15

WATCHDOG_DIR="/mnt/nvme/autoflip-data/logs/watchdog"
PID_FILE="$WATCHDOG_DIR/memory_watcher.pid"

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" || true
    sleep 1
    if kill -0 "$PID" 2>/dev/null; then
      kill -9 "$PID" || true
    fi
    echo "memory_watcher stopped pid=$PID"
  else
    echo "memory_watcher pid file existed but process was not running"
  fi
  rm -f "$PID_FILE"
else
  pkill -f '/memory_watcher.sh' 2>/dev/null || true
  echo "memory_watcher stopped if any matching process existed"
fi
