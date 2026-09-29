from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from shapely import intersects_xy
from shapely.geometry import Polygon


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "amsterdam_rules.json"


@lru_cache(maxsize=1)
def load_rules() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _snorfiets_zone() -> Polygon:
    return Polygon(load_rules()["snorfiets_zone"]["polygon"])


@lru_cache(maxsize=1)
def _emission_zone() -> Polygon:
    return Polygon(load_rules()["emission_zone"]["polygon"])


@lru_cache(maxsize=1)
def _exception_streets() -> frozenset[str]:
    return frozenset(item.casefold() for item in load_rules()["snorfiets_exception_streets"])


def point_in_snorfiets_zone(lon: float, lat: float) -> bool:
    # ``intersects_xy`` has the same boundary-inclusive behaviour as
    # ``polygon.covers(Point(...))`` for a point, without constructing a
    # Shapely Point for every road segment during graph preparation.
    return bool(intersects_xy(_snorfiets_zone(), lon, lat))


def point_in_emission_zone(lon: float, lat: float) -> bool:
    zone = load_rules()["emission_zone"]
    for west, south, east, north in zone.get("excluded_bboxes", []):
        if west <= lon <= east and south <= lat <= north:
            return False
    return bool(intersects_xy(_emission_zone(), lon, lat))


def is_exception_street(name: Any) -> bool:
    names = name if isinstance(name, (list, tuple, set)) else [name]
    return any(str(item).casefold() in _exception_streets() for item in names if item)


def prohibited_area_at(lon: float, lat: float) -> str | None:
    for area in load_rules()["prohibited_areas"]:
        west, south, east, north = area["bbox"]
        if west <= lon <= east and south <= lat <= north:
            return area["name"]
    return None


def local_metadata() -> dict[str, Any]:
    return load_rules()["metadata"]
