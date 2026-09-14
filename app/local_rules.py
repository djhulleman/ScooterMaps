from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from shapely.geometry import Point, Polygon


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "amsterdam_rules.json"


@lru_cache(maxsize=1)
def load_rules() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def point_in_snorfiets_zone(lon: float, lat: float) -> bool:
    coords = load_rules()["snorfiets_zone"]["polygon"]
    return Polygon(coords).covers(Point(lon, lat))


def point_in_emission_zone(lon: float, lat: float) -> bool:
    zone = load_rules()["emission_zone"]
    for west, south, east, north in zone.get("excluded_bboxes", []):
        if west <= lon <= east and south <= lat <= north:
            return False
    coords = zone["polygon"]
    return Polygon(coords).covers(Point(lon, lat))


def is_exception_street(name: Any) -> bool:
    names = name if isinstance(name, (list, tuple, set)) else [name]
    exceptions = {item.casefold() for item in load_rules()["snorfiets_exception_streets"]}
    return any(str(item).casefold() in exceptions for item in names if item)


def prohibited_area_at(lon: float, lat: float) -> str | None:
    for area in load_rules()["prohibited_areas"]:
        west, south, east, north = area["bbox"]
        if west <= lon <= east and south <= lat <= north:
            return area["name"]
    return None


def local_metadata() -> dict[str, Any]:
    return load_rules()["metadata"]
