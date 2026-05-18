#!/usr/bin/env bash
set -euo pipefail

echo "=== SYSTEMD ==="
systemctl --no-pager status autoflip-albion-collector.service || true

echo
echo "=== RUNNING PROCESS ==="
ps aux | grep -Ei "app.services.albion.collector_runner|albion_collector" | grep -v grep || true

echo
echo "=== LATEST LOG ==="
cat /mnt/nvme/autoflip-data/albion/logs/albion_collector_latest.json 2>/dev/null || true
