#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/home/osrscompanion/osrs-flip-assistant}"
DATA_ROOT="${OSRS_FLIP_DATA_ROOT:-/mnt/nvme/autoflip-data}"
INTERVAL="${OSRS_COLLECTOR_DIAG_INTERVAL_SECONDS:-30}"
LOG_DIR="$DATA_ROOT/logs/collector_diagnostics"
mkdir -p "$LOG_DIR"

cd "$APP_ROOT"
if [ -f "$APP_ROOT/venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$APP_ROOT/venv/bin/activate"
fi

export OSRS_FLIP_DATA_ROOT="$DATA_ROOT"
python -m app.services.collector_diagnostics scan-loop --interval-seconds "$INTERVAL"
