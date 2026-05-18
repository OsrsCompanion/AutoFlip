
#!/bin/bash
set -e

sudo tee /etc/systemd/system/autoflip-rs3-collector.service > /dev/null <<'EOF'
[Unit]
Description=AutoFlip RS3 market collector
After=network.target

[Service]
User=osrscompanion
WorkingDirectory=/home/osrscompanion/osrs-flip-assistant
Environment=PYTHONPATH=/home/osrscompanion/osrs-flip-assistant
Environment=OSRS_FLIP_DATA_ROOT=/mnt/nvme/autoflip-data
ExecStart=/home/osrscompanion/osrs-flip-assistant/venv/bin/python3 -m app.services.rs3.collector_runner
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable autoflip-rs3-collector
sudo systemctl restart autoflip-rs3-collector

echo "==================================="
echo "========= PATCH SUCCESS =========="
echo "==================================="
