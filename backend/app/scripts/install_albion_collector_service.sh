#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/osrscompanion/osrs-flip-assistant"
SERVICE_SRC="$PROJECT_ROOT/app/systemd/autoflip-albion-collector.service"
SERVICE_DST="/etc/systemd/system/autoflip-albion-collector.service"

if [[ ! -f "$SERVICE_SRC" ]]; then
  echo "missing service template: $SERVICE_SRC" >&2
  exit 1
fi

sudo cp "$SERVICE_SRC" "$SERVICE_DST"
sudo systemctl daemon-reload
sudo systemctl enable autoflip-albion-collector.service
sudo systemctl restart autoflip-albion-collector.service
sudo systemctl --no-pager status autoflip-albion-collector.service || true

echo "installed and started: autoflip-albion-collector.service"
