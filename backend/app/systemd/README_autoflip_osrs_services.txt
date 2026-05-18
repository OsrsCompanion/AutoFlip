AutoFlip OSRS Runtime Services Patch

Adds systemd units for:
- autoflip-osrs-collector.service
- autoflip-web.service
- autoflip-cache-freshness.service
- autoflip-collector-diagnostics.service

Install:
  cd /home/osrscompanion/osrs-flip-assistant
  bash app/scripts/install_osrs_runtime_services.sh

Status:
  bash app/scripts/status_osrs_runtime_services.sh

Restart:
  bash app/scripts/restart_osrs_runtime_services.sh

This patch does not modify OSRS collector, web, cache, or recommendation logic.
