from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "data" / "geocache.json"
LOCK = asyncio.Lock()


def _load_cache() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = CACHE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(CACHE_PATH)


async def geocode(query: str) -> tuple[list[dict[str, Any]], bool]:
    normalized = " ".join(query.casefold().split())
    cache = _load_cache()
    if normalized in cache:
        return cache[normalized], True

    params = {
        "q": f"{query}, Amsterdam, Nederland",
        "format": "jsonv2",
        "limit": 5,
        "countrycodes": "nl",
        "viewbox": "4.70,52.45,5.05,52.25",
        "bounded": 1,
        "addressdetails": 1,
    }
    headers = {"User-Agent": "ScooterMaps-local-poc/0.1 (personal local use)"}
    async with httpx.AsyncClient(timeout=8.0, headers=headers) as client:
        response = await client.get("https://nominatim.openstreetmap.org/search", params=params)
        response.raise_for_status()
        values = [
            {
                "label": item["display_name"],
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
            }
            for item in response.json()
        ]
    async with LOCK:
        cache = _load_cache()
        cache[normalized] = values
        _save_cache(cache)
    return values, False

