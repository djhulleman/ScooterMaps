"""Prepare the real Amsterdam OSM/NDW caches outside the running web process."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.data_manager import network_manager


if __name__ == "__main__":
    network_manager._prepare()
    status = network_manager.status()
    print(json.dumps(status, ensure_ascii=False))
    raise SystemExit(0 if status.get("mode") == "osm" else 1)

