from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OVERRIDES_PATH = ROOT / "data" / "local_overrides.geojson"
LOCK = threading.Lock()


def _empty() -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": []}


def load_collection() -> dict[str, Any]:
    with LOCK:
        if not OVERRIDES_PATH.exists():
            return _empty()
        try:
            value = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
            if value.get("type") != "FeatureCollection":
                return _empty()
            return value
        except (json.JSONDecodeError, OSError):
            return _empty()


def save_collection(collection: dict[str, Any]) -> None:
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = OVERRIDES_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(collection, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(OVERRIDES_PATH)


def add_override(feature: dict[str, Any]) -> dict[str, Any]:
    with LOCK:
        collection = _empty()
        if OVERRIDES_PATH.exists():
            try:
                collection = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                collection = _empty()
        feature = dict(feature)
        feature["type"] = "Feature"
        properties = dict(feature.get("properties", {}))
        properties["id"] = properties.get("id") or str(uuid.uuid4())
        feature["properties"] = properties
        collection.setdefault("features", []).append(feature)
        save_collection_unlocked(collection)
        return feature


def update_override(item_id: str, properties: dict[str, Any]) -> dict[str, Any] | None:
    with LOCK:
        collection = load_collection_unlocked()
        for feature in collection.get("features", []):
            if feature.get("properties", {}).get("id") == item_id:
                feature["properties"].update(properties)
                feature["properties"]["id"] = item_id
                save_collection_unlocked(collection)
                return feature
    return None


def delete_override(item_id: str) -> bool:
    with LOCK:
        collection = load_collection_unlocked()
        before = len(collection.get("features", []))
        collection["features"] = [
            feature for feature in collection.get("features", [])
            if feature.get("properties", {}).get("id") != item_id
        ]
        if len(collection["features"]) == before:
            return False
        save_collection_unlocked(collection)
        return True


def load_collection_unlocked() -> dict[str, Any]:
    if not OVERRIDES_PATH.exists():
        return _empty()
    try:
        return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty()


def save_collection_unlocked(collection: dict[str, Any]) -> None:
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = OVERRIDES_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(collection, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(OVERRIDES_PATH)

