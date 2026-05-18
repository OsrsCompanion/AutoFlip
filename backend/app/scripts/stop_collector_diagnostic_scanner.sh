#!/usr/bin/env bash
set -euo pipefail

DATA_ROOT="${OSRS_FLIP_DATA_ROOT:-/mnt/nvme/autoflip-data}"
LOG_DIR="$DATA_ROOT/logs/collector_diagnostics"
PID_FILE="$LOG_DIR/scanner.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "collector diagnostic scanner pid file not found"
  exit 0
fi

pid="$(cat "$PID_FILE" || true)"
if [ -z "$pid" ]; then
  rm -f "$PID_FILE"
  echo "collector diagnostic scanner pid file was empty"
  exit 0
fi

if kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  echo "collector diagnostic scanner stopped pid=$pid"
else
  echo "collector diagnostic scanner was not running pid=$pid"
fi
rm -f "$PID_FILE"
