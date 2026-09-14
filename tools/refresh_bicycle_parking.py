from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "bicycle_parking.json"


def main() -> int:
    try:
        import osmnx as ox

        west, south, east, north = 4.72, 52.29, 5.02, 52.43
        try:
            frame = ox.features_from_bbox((west, south, east, north), {"amenity": "bicycle_parking"})
        except TypeError:
            frame = ox.features_from_bbox(north, south, east, west, tags={"amenity": "bicycle_parking"})
        features = []
        for osm_key, row in frame.iterrows():
            geometry = row.geometry
            point = geometry if geometry.geom_type == "Point" else geometry.centroid
            properties = {
                "id": f"osm-{osm_key}",
                "name": row.get("name") if isinstance(row.get("name"), str) else "Fietsparkeren",
                "capacity": int(row["capacity"]) if str(row.get("capacity", "")).isdigit() else None,
                "covered": str(row.get("covered", "")).lower() == "yes" if row.get("covered") is not None else None,
                "secure": str(row.get("supervised", "")).lower() == "yes" if row.get("supervised") is not None else None,
                "opening_hours": row.get("opening_hours") if isinstance(row.get("opening_hours"), str) else "onbekend",
                "access": row.get("access") if isinstance(row.get("access"), str) else None,
                "confidence": "middel",
                "source": "OpenStreetMap",
            }
            features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(point.x), float(point.y)]}, "properties": properties})
        payload = {
            "type": "FeatureCollection",
            "metadata": {"source": "OpenStreetMap amenity=bicycle_parking", "created_at": datetime.now(timezone.utc).isoformat()},
            "features": features,
        }
        temporary = OUTPUT.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(OUTPUT)
        print(f"{len(features)} fietsparkeerlocaties opgeslagen in {OUTPUT}")
        return 0
    except Exception as exc:
        print(f"FOUT: fietsparkeerdata niet vernieuwd: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
