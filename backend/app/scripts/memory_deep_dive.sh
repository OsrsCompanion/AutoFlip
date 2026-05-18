#!/bin/bash
# AUTOFLIP MEMORY DEEP DIVE v25 (restore original zip + bundle)

LOG_ROOT="/mnt/nvme/autoflip-data/logs"
MD_DIR="$LOG_ROOT/memory_deep_dive"
DIVES_DIR="$MD_DIR/dives"
BUNDLES_DIR="$MD_DIR/bundles"
WATCHDOG_DIR="$LOG_ROOT/watchdog"
DIAG_BUNDLE_DIR="$LOG_ROOT/diagnostic_bundle"

mkdir -p "$DIVES_DIR"
mkdir -p "$BUNDLES_DIR"
mkdir -p "$DIAG_BUNDLE_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$DIVES_DIR/memory_deep_dive_raspberrypi_$TIMESTAMP.log"
ZIP_FILE="$BUNDLES_DIR/memory_deep_dive_raspberrypi_$TIMESTAMP.zip"

# ---- CORE LOG ----
echo "=== MEMORY DEEP DIVE v25 ===" > "$LOG_FILE"
date >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

free -h >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

ps aux --sort=-%mem | head -20 >> "$LOG_FILE"

# maintain latest.log
cp "$LOG_FILE" "$MD_DIR/latest.log"

# ---- ORIGINAL REQUIRED ZIP (CRITICAL FOR TEST) ----
zip -j "$ZIP_FILE" "$LOG_FILE" >/dev/null 2>&1

# ---- NEW SUMMARY ----
SUMMARY_FILE="/tmp/diag_summary_$TIMESTAMP.txt"

echo "=== DIAGNOSTIC SUMMARY ===" > "$SUMMARY_FILE"
free -h >> "$SUMMARY_FILE"
ps aux --sort=-%mem | head -20 >> "$SUMMARY_FILE"
du -sh /mnt/nvme/autoflip-data/* 2>/dev/null >> "$SUMMARY_FILE"
tail -n 50 "$WATCHDOG_DIR"/memory_watcher.log 2>/dev/null >> "$SUMMARY_FILE"

# ---- NEW DIAGNOSTIC BUNDLE ----
DIAG_BUNDLE="$DIAG_BUNDLE_DIR/diagnostic_bundle_$TIMESTAMP.zip"

zip -r "$DIAG_BUNDLE"   "$MD_DIR/latest.log"   "$DIVES_DIR"   "$WATCHDOG_DIR"   "$SUMMARY_FILE"   -x "*.pid" >/dev/null 2>&1

# ---- RETURN CONTRACT ----
echo "$LOG_FILE"
