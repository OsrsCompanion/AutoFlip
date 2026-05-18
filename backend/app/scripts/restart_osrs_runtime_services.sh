#!/usr/bin/env bash
set -euo pipefail

SERVICES=(
  "autoflip-osrs-collector.service"
  "autoflip-web.service"
  "autoflip-cache-freshness.service"
  "autoflip-collector-diagnostics.service"
)

for svc in "${SERVICES[@]}"; do
  echo "restarting: $svc"
  sudo systemctl restart "$svc"
done

bash /home/osrscompanion/osrs-flip-assistant/app/scripts/status_osrs_runtime_services.sh
