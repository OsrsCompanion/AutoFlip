
import json
import os
from datetime import datetime, timezone
from pathlib import Path

DATA_ROOT = Path(os.getenv("OSRS_FLIP_DATA_ROOT", "/mnt/nvme/autoflip-data"))
RS3_ROOT = DATA_ROOT / "rs3_market_history"

for sub in ["raw", "snapshots", "events", "summaries", "archive"]:
    (RS3_ROOT / sub).mkdir(parents=True, exist_ok=True)

snapshot = {
    "game": "rs3",
    "snapshot_ts": datetime.now(timezone.utc).isoformat(),
    "status": "collector_initialized"
}

today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
with open(RS3_ROOT / "current_snapshot.json", "w") as f:
    json.dump(snapshot, f)

with open(RS3_ROOT / "snapshots" / f"{today}.jsonl", "a") as f:
    f.write(json.dumps(snapshot) + "\n")

print("[rs3_collector] initialized rs3 market history layout")
