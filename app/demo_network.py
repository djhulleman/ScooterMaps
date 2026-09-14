from __future__ import annotations

import math

import networkx as nx


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1 = a
    lon2, lat2 = b
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return math.sqrt(x * x + y * y) * 6_371_000


def build_demo_graph() -> nx.MultiDiGraph:
    """Small connected Amsterdam-shaped graph used while OSM prepares or is offline."""
    graph = nx.MultiDiGraph(crs="EPSG:4326", name="ScooterMaps demonstratienetwerk")
    nodes = {
        1: (4.8787, 52.3640, "Leidseplein"),
        2: (4.8920, 52.3660, "Muntplein"),
        3: (4.9020, 52.3670, "Waterlooplein"),
        4: (4.9180, 52.3460, "Amstelstation"),
        5: (4.8880, 52.3590, "Museumbrug"),
        6: (4.9030, 52.3570, "Westeinde"),
        7: (4.8890, 52.3720, "Rozengracht"),
        8: (4.9050, 52.3740, "Oosterdok"),
        9: (4.9000, 52.3795, "Centraal Station"),
        10: (4.9050, 52.3920, "Buiksloterweg"),
        11: (4.9510, 52.3730, "Sporenburg"),
        12: (4.9660, 52.3780, "Zeeburgereiland"),
        13: (4.8670, 52.3610, "Vondelpark noord"),
        14: (4.8530, 52.3680, "Oud-West"),
    }
    for node_id, (lon, lat, label) in nodes.items():
        graph.add_node(node_id, x=lon, y=lat, label=label)

    def add(a: int, b: int, **tags: object) -> None:
        coords_a = nodes[a][:2]
        coords_b = nodes[b][:2]
        attrs = {
            "length": round(_distance(coords_a, coords_b), 1),
            "osmid": f"demo-{min(a,b)}-{max(a,b)}",
            **tags,
        }
        graph.add_edge(a, b, **attrs)
        graph.add_edge(b, a, **attrs)

    # Fast direct option for snorfiets: an Amsterdam exception corridor.
    add(1, 5, highway="cycleway", name="Stadhouderskade", traffic_sign="NL:G11", bicycle="designated")
    add(5, 6, highway="cycleway", name="Stadhouderskade", traffic_sign="NL:G11", bicycle="designated")
    add(6, 4, highway="cycleway", name="Stadhouderskade", traffic_sign="NL:G11", bicycle="designated")

    # Main roadway option for the bromfiets.
    add(1, 2, highway="tertiary", name="Vijzelgracht", maxspeed="30")
    add(2, 3, highway="secondary", name="Waterloopleinroute", maxspeed="50")
    add(3, 4, highway="secondary", name="Wibautstraat", maxspeed="50")

    # Quieter shared alternative and a G12a connector.
    add(1, 7, highway="residential", name="Jordaanroute", maxspeed="30")
    add(7, 8, highway="residential", name="Kadesroute", maxspeed="30")
    add(8, 3, highway="cycleway", name="Oosterdokspad", traffic_sign="NL:G12a", moped="designated", mofa="designated")
    add(8, 4, highway="cycleway", name="Veilige Amstelroute", traffic_sign="NL:G12a", moped="designated", mofa="designated")
    add(8, 9, highway="service", name="De Ruijterkade", maxspeed="30")
    add(8, 9, highway="cycleway", name="De Ruijterkade", traffic_sign="NL:G11", bicycle="designated")

    # One permitted ferry and F9, which must never be routed for scooters.
    add(9, 10, highway="ferry", route="ferry", name="F3 Buiksloterwegveer", moped="yes", mofa="yes", duration="00:05")
    add(11, 12, highway="ferry", route="ferry", name="F9 Sporenburg – Zeeburgereiland", bicycle="yes", moped="no", mofa="no")

    # Edges around (not through) Vondelpark keep the demo connected in west.
    add(1, 13, highway="residential", name="Overtoom", maxspeed="30")
    add(13, 14, highway="residential", name="Postjesweg", maxspeed="30")
    add(14, 7, highway="residential", name="Kinkerstraat", maxspeed="30")
    return graph
