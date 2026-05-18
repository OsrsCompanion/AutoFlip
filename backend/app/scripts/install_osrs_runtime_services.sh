#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/home/osrscompanion/osrs-flip-assistant"
SYSTEMD_SRC="$APP_ROOT/app/systemd"

SERVICES=(
  "autoflip-osrs-collector.service"
  "autoflip-web.service"
  "autoflip-cache-freshness.service"
  "autoflip-collector-diagnostics.service"
)

echo "=== INSTALL AUTOFLIP OSRS RUNTIME SYSTEMD SERVICES ==="

if [ ! -d "$APP_ROOT" ]; then
  echo "ERROR: missing app root: $APP_ROOT" >&2
  exit 1
fi

if [ ! -x "$APP_ROOT/venv/bin/python3" ]; then
  echo "ERROR: missing venv python: $APP_ROOT/venv/bin/python3" >&2
  exit 1
fi

for svc in "${SERVICES[@]}"; do
  if [ ! -f "$SYSTEMD_SRC/$svc" ]; then
    echo "ERROR: missing service file: $SYSTEMD_SRC/$svc" >&2
    exit 1
  fi
done

echo
echo "=== STOP MANUAL PROCESSES BEFORE SERVICE START ==="
pkill -TERM -f "$APP_ROOT/venv/bin/python3 -m app.services.collector_runner" 2>/dev/null || true
pkill -TERM -f "$APP_ROOT/venv/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000" 2>/dev/null || true
pkill -TERM -f "python3 -m app.services.cache_freshness_monitor --interval 300 --mode mtime" 2>/dev/null || true
pkill -TERM -f "python -m app.services.collector_diagnostics scan-loop --interval-seconds 30" 2>/dev/null || true
pkill -TERM -f "python3 -m app.services.collector_diagnostics scan-loop --interval-seconds 30" 2>/dev/null || true
sleep 3

echo
echo "=== COPY SERVICE FILES ==="
for svc in "${SERVICES[@]}"; do
  sudo cp "$SYSTEMD_SRC/$svc" "/etc/systemd/system/$svc"
  sudo chmod 0644 "/etc/systemd/system/$svc"
  echo "installed: /etc/systemd/system/$svc"
done

echo
echo "=== ENABLE + START SERVICES ==="
sudo systemctl daemon-reload

for svc in "${SERVICES[@]}"; do
  sudo systemctl enable "$svc"
  sudo systemctl restart "$svc"
done

echo
echo "=== STATUS ==="
for svc in "${SERVICES[@]}"; do
  echo
  echo "--- $svc ---"
  systemctl --no-pager --full status "$svc" | sed -n '1,14p'
done

echo
echo "done: OSRS collector, web, freshness monitor, and diagnostics are now systemd-managed."
