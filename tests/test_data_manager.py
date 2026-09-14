from app.data_manager import CACHE_SCHEMA_VERSION, ROUTING_WAY_TAGS


def test_osm_import_keeps_vehicle_and_sidepath_access_tags():
    assert CACHE_SCHEMA_VERSION >= 2
    assert {
        "bicycle",
        "mofa",
        "moped",
        "traffic_sign",
        "oneway:mofa",
        "oneway:moped",
    }.issubset(ROUTING_WAY_TAGS)
