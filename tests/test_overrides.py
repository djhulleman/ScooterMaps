import uuid
from pathlib import Path

from app import overrides


def test_local_override_lifecycle(monkeypatch):
    target = Path("data") / f".test-overrides-{uuid.uuid4()}.geojson"
    monkeypatch.setattr(overrides, "OVERRIDES_PATH", target)
    try:
        feature = overrides.add_override({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[4.9, 52.36], [4.91, 52.37]]},
            "properties": {
                "osm_id": "test-1",
                "name": "Testweg",
                "vehicle": "snorfiets",
                "correction": "forbidden_snorfiets",
                "permanent": False,
            },
        })
        item_id = feature["properties"]["id"]
        assert target.exists()
        assert len(overrides.load_collection()["features"]) == 1
        assert overrides.update_override(item_id, {"permanent": True})["properties"]["permanent"] is True
        assert overrides.delete_override(item_id) is True
        assert overrides.load_collection()["features"] == []
    finally:
        target.unlink(missing_ok=True)
