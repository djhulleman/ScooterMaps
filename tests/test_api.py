from fastapi.testclient import TestClient

from app.data_manager import network_manager
from app.demo_network import build_demo_graph
from app.main import app


def test_health_and_metadata_endpoints():
    with TestClient(app) as client:
        health = client.get("/api/health")
        metadata = client.get("/api/metadata")
    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert metadata.status_code == 200
    assert metadata.json()["local_rules"]["checked_on"] == "2026-09-14"


def test_route_endpoint_returns_colored_segments():
    network_manager._graph = build_demo_graph()
    network_manager._mode = "demo"
    network_manager._status.update({"mode": "demo", "state": "ready"})
    request = {
        "start": {"lat": 52.3640, "lon": 4.8787},
        "end": {"lat": 52.3460, "lon": 4.9180},
        "vehicle": "snorfiets",
        "preference": "fast",
        "powertrain": "electric",
        "build_year": 2020,
        "exemption": False,
    }
    with TestClient(app) as client:
        response = client.post("/api/route", json=request)
    assert response.status_code == 200
    payload = response.json()
    assert payload["routes"]
    assert payload["routes"][0]["geometry"]["features"]


def test_bicycle_journey_endpoint_preserves_dedicated_profile():
    network_manager._graph = build_demo_graph()
    network_manager._mode = "demo"
    network_manager._status.update({"mode": "demo", "state": "ready"})
    request = {
        "start": {"lat": 52.3640, "lon": 4.8787},
        "end": {"lat": 52.3460, "lon": 4.9180},
        "mode": "bike_all",
        "bicycle_type": "regular",
        "preference": "comfortable",
        "departure_time": "2026-09-14T11:00:00+02:00",
    }
    with TestClient(app) as client:
        response = client.post("/api/journey", json=request)
    assert response.status_code == 200
    option = response.json()["options"][0]
    assert option["type"] == "bike_all"
    assert option["segments"]
    assert all(segment["mode"] in {"bike", "bike_walk"} for segment in option["segments"])


def test_transit_status_is_exposed():
    with TestClient(app) as client:
        response = client.get("/api/transit/status")
    assert response.status_code == 200
    assert response.json()["state"] in {"ready", "missing", "invalid"}
