#!/usr/bin/env bash
set -euo pipefail
cd /home/osrscompanion/osrs-flip-assistant
PYTHONPATH=. AUTOFLIP_DATA_ROOT=/mnt/nvme/autoflip-data ./venv/bin/python3 -m app.services.albion.collector_runner --once
PYTHONPATH=. ./venv/bin/python3 app/tools/verify_albion_collector.py
