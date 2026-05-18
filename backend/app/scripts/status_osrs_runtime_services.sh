#!/usr/bin/env bash
set -euo pipefail

SERVICES=(
  "autoflip-osrs-collector.service"
  "autoflip-web.service"
  "autoflip-cache-freshness.service"
  "autoflip-collector-diagnostics.service"
  "autoflip-albion-collector.service"
)

echo "=== SYSTEMD UNIT FILES ==="
systemctl list-unit-files | grep -Ei "autoflip-(osrs|web|cache|collector|albion)" || true

echo
echo "=== SERVICE STATUS ==="
for svc in "${SERVICES[@]}"; do
  echo
  echo "--- $svc ---"
  systemctl --no-pager --full status "$svc" 2>/dev/null | sed -n '1,18p' || echo "missing: $svc"
done

echo
echo "=== RUNNING AUTOFLIP PYTHON PROCESSES ==="
ps aux | grep -Ei "collector_runner|uvicorn app.main|cache_freshness_monitor|collector_diagnostics|albion.collector_runner" | grep -v grep || true

echo
echo "=== ENABLED CUSTOM AUTOFLIP SERVICES ==="
find /etc/systemd/system -maxdepth 2 \( -type f -o -type l \) | grep -Ei "autoflip" | sort || true
