#!/usr/bin/env bash
set -euo pipefail

DATA_ROOT="${OSRS_FLIP_DATA_ROOT:-/mnt/nvme/autoflip-data}"
LOG_DIR="$DATA_ROOT/logs/collector_diagnostics"
PID_FILE="$LOG_DIR/scanner.pid"

if [ -f "$PID_FILE" ]; then
  pid="$(cat "$PID_FILE" || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "collector diagnostic scanner running pid=$pid"
  else
    echo "collector diagnostic scanner pid file exists but process is not running pid=$pid"
  fi
else
  echo "collector diagnostic scanner not running"
fi

if [ -f "$LOG_DIR/latest.log" ]; then
  echo "--- latest.log ---"
  cat "$LOG_DIR/latest.log"
fi
