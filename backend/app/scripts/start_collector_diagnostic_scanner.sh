#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/home/osrscompanion/osrs-flip-assistant}"
DATA_ROOT="${OSRS_FLIP_DATA_ROOT:-/mnt/nvme/autoflip-data}"
LOG_DIR="$DATA_ROOT/logs/collector_diagnostics"
PID_FILE="$LOG_DIR/scanner.pid"
OUT_FILE="$LOG_DIR/scanner.out.log"
mkdir -p "$LOG_DIR"

if [ -f "$PID_FILE" ]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    echo "collector diagnostic scanner already running pid=$old_pid"
    exit 0
  fi
fi

nohup bash "$APP_ROOT/app/scripts/collector_diagnostic_scanner.sh" >> "$OUT_FILE" 2>&1 &
pid=$!
echo "$pid" > "$PID_FILE"
echo "collector diagnostic scanner started pid=$pid"
echo "latest log: $LOG_DIR/latest.log"
echo "jsonl log: $LOG_DIR/scanner.jsonl"
