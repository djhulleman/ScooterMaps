from app.data_manager import network_manager
from app.demo_network import build_demo_graph
from app.routing import VehicleSettings, calculate_routes


def use_demo_graph():
    network_manager._graph = build_demo_graph()
    network_manager._mode = "demo"
    network_manager._status.update({"mode": "demo", "state": "ready"})


def route_edge_ids(payload):
    return {
        feature["properties"]["osm_id"]
        for feature in payload["routes"][0]["geometry"]["features"]
    }


def test_demo_trip_differs_for_snorfiets_and_bromfiets():
    use_demo_graph()
    start = (52.3640, 4.8787)
    end = (52.3460, 4.9180)
    snor = calculate_routes(start, end, VehicleSettings("snorfiets"), "fast")
    brom = calculate_routes(start, end, VehicleSettings("bromfiets"), "fast")
    assert route_edge_ids(snor) != route_edge_ids(brom)
    assert any(
        feature["properties"]["place"] == "verplicht fietspad"
        for feature in snor["routes"][0]["geometry"]["features"]
    )
    assert all(
        feature["properties"]["place"] != "verplicht fietspad"
        for feature in brom["routes"][0]["geometry"]["features"]
    )


def test_quiet_route_avoids_fast_secondary_when_an_alternative_exists():
    use_demo_graph()
    result = calculate_routes(
        (52.3640, 4.8787),
        (52.3460, 4.9180),
        VehicleSettings("bromfiets"),
        "quiet",
    )
    names = {feature["properties"]["name"] for feature in result["routes"][0]["geometry"]["features"]}
    assert "Jordaanroute" in names or "Kadesroute" in names


def test_permitted_ferry_includes_motor_off_instruction():
    use_demo_graph()
    result = calculate_routes(
        (52.3795, 4.9000),
        (52.3920, 4.9050),
        VehicleSettings("snorfiets"),
        "fast",
    )
    texts = [item["text"].lower() for item in result["routes"][0]["instructions"]]
    assert any("motor uit" in text for text in texts)

