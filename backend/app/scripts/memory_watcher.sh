#!/usr/bin/env bash
set -euo pipefail

# AUTOFLIP MEMORY WATCHER v15

THRESHOLD_PERCENT="${MEMORY_WATCHER_THRESHOLD_PERCENT:-10}"
CHECK_SECONDS="${MEMORY_WATCHER_CHECK_SECONDS:-30}"
COOLDOWN_SECONDS="${MEMORY_WATCHER_COOLDOWN_SECONDS:-300}"
BASE_LOG_DIR="/mnt/nvme/autoflip-data/logs"
WATCHDOG_DIR="$BASE_LOG_DIR/watchdog"
LOG_FILE="$WATCHDOG_DIR/memory_watcher.log"
PID_FILE="$WATCHDOG_DIR/memory_watcher.pid"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEEP_DIVE="$SCRIPT_DIR/memory_deep_dive.sh"
LAST_RUN=0

mkdir -p "$WATCHDOG_DIR"
echo $$ > "$PID_FILE"

ts() { date --iso-8601=seconds; }
log() { echo "$(ts) $*" >> "$LOG_FILE"; }

cleanup() {
  rm -f "$PID_FILE"
}
trap cleanup EXIT

log "memory_watcher v15 started threshold=${THRESHOLD_PERCENT}% check=${CHECK_SECONDS}s cooldown=${COOLDOWN_SECONDS}s deep_dive=${DEEP_DIVE}"

while true; do
  if ! MEM_AVAILABLE_PERCENT="$(free | awk '/Mem:/ { if ($2 > 0) printf "%.2f", ($7 / $2) * 100; else print "0" }')"; then
    log "WARN failed to read memory; running deep dive fallback"
    "$DEEP_DIVE" >> "$LOG_FILE" 2>&1 || log "ERROR deep dive failed"
    sleep "$CHECK_SECONDS"
    continue
  fi

  NOW=$(date +%s)
  SHOULD_TRIGGER=$(awk -v a="$MEM_AVAILABLE_PERCENT" -v t="$THRESHOLD_PERCENT" 'BEGIN { print (a <= t) ? 1 : 0 }')

  if [ "$SHOULD_TRIGGER" = "1" ]; then
    AGE=$((NOW - LAST_RUN))
    if [ "$LAST_RUN" -eq 0 ] || [ "$AGE" -ge "$COOLDOWN_SECONDS" ]; then
      log "TRIGGER available_memory=${MEM_AVAILABLE_PERCENT}% threshold=${THRESHOLD_PERCENT}%"
      "$DEEP_DIVE" >> "$LOG_FILE" 2>&1 || log "ERROR deep dive failed"
      LAST_RUN="$NOW"
    else
      log "COOLDOWN available_memory=${MEM_AVAILABLE_PERCENT}% age=${AGE}s cooldown=${COOLDOWN_SECONDS}s"
    fi
  else
    log "OK available_memory=${MEM_AVAILABLE_PERCENT}% threshold=${THRESHOLD_PERCENT}%"
  fi

  sleep "$CHECK_SECONDS"
done
