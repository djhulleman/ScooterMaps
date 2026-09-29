from __future__ import annotations

import math
import threading
import uuid
from dataclasses import asdict, dataclass
from typing import Any

import networkx as nx
from shapely import get_coordinates, get_num_coordinates
from .data_manager import network_manager
from .emissions import evaluate_emission_access
from .local_rules import is_exception_street, point_in_emission_zone, point_in_snorfiets_zone, prohibited_area_at
from .overrides import OVERRIDES_PATH, load_collection
from .rules import AccessDecision, evaluate_edge_access


TAG_KEYS = (
    "osmid", "name", "highway", "cycleway", "bicycle", "mofa", "moped",
    "cycleway:both", "cycleway:left", "cycleway:right", "vehicle",
    "motor_vehicle", "access", "oneway", "oneway:mofa", "oneway:moped",
    "oneway:bicycle", "maxspeed", "traffic_sign", "source:traffic_sign",
    "motorroad", "junction", "route", "duration",
)
CONFIDENCE_ORDER = {"hoog": 0, "middel": 1, "laag": 2}
_LEGAL_GRAPH_CACHE: dict[tuple[Any, ...], nx.MultiDiGraph] = {}
_LEGAL_GRAPH_CACHE_LOCK = threading.Lock()
_ACCESS_DECISION_TAGS = (
    "highway", "traffic_sign", "mofa", "moped", "access", "vehicle",
    "motor_vehicle", "oneway", "oneway:mofa", "oneway:moped", "route",
    "maxspeed", "motorroad", "bicycle",
)


@dataclass(frozen=True)
class VehicleSettings:
    profile: str
    powertrain: str = "electric"
    build_year: int = 2020
    exemption: bool = False


def _stringify(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set)):
        return "; ".join(str(item) for item in value)
    return str(value)


def _edge_coords(graph: nx.MultiDiGraph, u: Any, v: Any, data: dict[str, Any]) -> list[list[float]]:
    geometry = data.get("geometry")
    if geometry is not None and hasattr(geometry, "coords"):
        coords = [[float(x), float(y)] for x, y in geometry.coords]
        u_coord = [float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"])]
        direct = abs(coords[0][0] - u_coord[0]) + abs(coords[0][1] - u_coord[1])
        reverse = abs(coords[-1][0] - u_coord[0]) + abs(coords[-1][1] - u_coord[1])
        if reverse < direct:
            coords.reverse()
        return coords
    return [
        [float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"])],
        [float(graph.nodes[v]["x"]), float(graph.nodes[v]["y"])],
    ]


def _midpoint(graph: nx.MultiDiGraph, u: Any, v: Any, data: dict[str, Any]) -> tuple[float, float]:
    geometry = data.get("geometry")
    if geometry is not None and hasattr(geometry, "coords"):
        sequence = geometry.coords
        x, y = sequence[len(sequence) // 2]
        return float(x), float(y)
    return (
        (float(graph.nodes[u]["x"]) + float(graph.nodes[v]["x"])) / 2,
        (float(graph.nodes[u]["y"]) + float(graph.nodes[v]["y"])) / 2,
    )


def _edge_midpoints(
    graph: nx.MultiDiGraph,
    edges: list[tuple[Any, Any, Any, dict[str, Any]]],
) -> list[tuple[float, float]]:
    """Return the same line midpoints as ``_midpoint`` in one vectorised pass."""
    result: list[tuple[float, float] | None] = [None] * len(edges)
    geometries: list[Any] = []
    geometry_indexes: list[int] = []

    for index, (u, v, _, data) in enumerate(edges):
        geometry = data.get("geometry")
        if geometry is not None and hasattr(geometry, "coords"):
            geometries.append(geometry)
            geometry_indexes.append(index)
        else:
            result[index] = (
                (float(graph.nodes[u]["x"]) + float(graph.nodes[v]["x"])) / 2,
                (float(graph.nodes[u]["y"]) + float(graph.nodes[v]["y"])) / 2,
            )

    if geometries:
        coordinate_counts = get_num_coordinates(geometries)
        coordinates = get_coordinates(geometries)
        offset = 0
        for index, coordinate_count in zip(geometry_indexes, coordinate_counts):
            count = int(coordinate_count)
            lon, lat = coordinates[offset + count // 2]
            result[index] = (float(lon), float(lat))
            offset += count

    return [point for point in result if point is not None]


def _nearest_node(graph: nx.MultiDiGraph, lon: float, lat: float, *, for_start: bool) -> tuple[Any, float]:
    best_node: Any = None
    best_distance = float("inf")
    cos_lat = math.cos(math.radians(lat))
    routing_nodes = graph.graph.get("_routing_nodes")
    candidates = routing_nodes if routing_nodes else graph.nodes
    for node in candidates:
        data = graph.nodes[node]
        if for_start and graph.out_degree(node) == 0:
            continue
        if not for_start and graph.in_degree(node) == 0:
            continue
        dx = (float(data["x"]) - lon) * 111_320 * cos_lat
        dy = (float(data["y"]) - lat) * 110_540
        distance = math.hypot(dx, dy)
        if distance < best_distance:
            best_node, best_distance = node, distance
    if best_node is None:
        raise ValueError("Het routenetwerk bevat geen punten.")
    return best_node, best_distance


def _safety_factor(data: dict[str, Any], decision: AccessDecision) -> float:
    highway = str(data.get("highway", ""))
    factor = 1.0
    if highway in {"primary", "primary_link"}:
        factor += 0.75
    elif highway in {"secondary", "secondary_link"}:
        factor += 0.68
    elif highway in {"tertiary", "tertiary_link"}:
        factor += 0.18
    elif highway in {"residential", "living_street", "cycleway"}:
        factor -= 0.08
    if decision.legal_speed_kmh >= 50:
        factor += 0.28
    if decision.confidence == "laag":
        factor += 0.80
    elif decision.confidence == "middel":
        factor += 0.12
    return max(0.75, factor)


def _build_legal_graph(graph: nx.MultiDiGraph, settings: VehicleSettings) -> nx.MultiDiGraph:
    override_stamp = OVERRIDES_PATH.stat().st_mtime_ns if OVERRIDES_PATH.exists() else 0
    emission = evaluate_emission_access(settings.powertrain, settings.build_year, settings.exemption)
    # Keep the graph object itself in the key. Using id(graph) can collide after
    # short-lived synthetic test graphs are garbage-collected.
    cache_key = (graph, settings.profile, emission.allowed, override_stamp)
    with _LEGAL_GRAPH_CACHE_LOCK:
        cached = _LEGAL_GRAPH_CACHE.get(cache_key)
    if cached is not None:
        return cached

    legal = nx.MultiDiGraph()
    legal.graph.update(graph.graph)
    legal.add_nodes_from(graph.nodes(data=True))
    overrides = load_collection().get("features", [])
    has_permanent_override = any(
        feature.get("properties", feature).get("permanent", False)
        for feature in overrides
    )
    decision_cache: dict[tuple[Any, ...], AccessDecision] = {}
    raw_edges = list(graph.edges(keys=True, data=True))
    midpoints = _edge_midpoints(graph, raw_edges)

    for (u, v, key, raw_data), (lon, lat) in zip(raw_edges, midpoints):
        data = dict(raw_data)
        zone = point_in_snorfiets_zone(lon, lat)
        exception = is_exception_street(data.get("name"))
        prohibited = prohibited_area_at(lon, lat)
        if has_permanent_override:
            decision = evaluate_edge_access(
                data,
                settings.profile,
                inside_snorfiets_zone=zone,
                exception_street=exception,
                prohibited_area=prohibited,
                overrides=overrides,
            )
        else:
            decision_key = (
                zone,
                exception,
                prohibited,
                "f9" in str(data.get("name", "")).lower(),
                *(_stringify(data.get(tag)) for tag in _ACCESS_DECISION_TAGS),
            )
            decision = decision_cache.get(decision_key)
            if decision is None:
                decision = evaluate_edge_access(
                    data,
                    settings.profile,
                    inside_snorfiets_zone=zone,
                    exception_street=exception,
                    prohibited_area=prohibited,
                    overrides=overrides,
                )
                decision_cache[decision_key] = decision
        if decision.allowed is False:
            continue
        if not emission.allowed and point_in_emission_zone(lon, lat):
            continue

        length = max(float(data.get("length", 1.0) or 1.0), 1.0)
        speed = max(decision.routing_speed_kmh, 3.0)
        travel_time = length / (speed * 1000 / 3600)
        data["_decision"] = decision
        data["travel_time"] = travel_time
        data["quiet_cost"] = travel_time * _safety_factor(data, decision)
        legal.add_edge(u, v, key=key, **data)
    components = nx.strongly_connected_components(legal)
    legal.graph["_routing_nodes"] = max(components, key=len, default=set())
    with _LEGAL_GRAPH_CACHE_LOCK:
        if len(_LEGAL_GRAPH_CACHE) >= 4:
            _LEGAL_GRAPH_CACHE.clear()
        _LEGAL_GRAPH_CACHE[cache_key] = legal
    return legal


def _choose_edges(graph: nx.MultiDiGraph, nodes: list[Any], weight: str) -> list[tuple[Any, Any, Any, dict[str, Any]]]:
    chosen = []
    for u, v in zip(nodes, nodes[1:]):
        candidates = graph.get_edge_data(u, v) or {}
        if not candidates:
            raise nx.NetworkXNoPath
        key, data = min(candidates.items(), key=lambda item: float(item[1].get(weight, float("inf"))))
        chosen.append((u, v, key, data))
    return chosen


def _path_with_penalties(
    graph: nx.MultiDiGraph,
    start: Any,
    end: Any,
    weight: str,
    penalized_pairs: set[tuple[Any, Any]],
) -> list[Any]:
    def weighted(u: Any, v: Any, edge_bundle: dict[Any, dict[str, Any]]) -> float:
        values = [float(data.get(weight, 1.0)) for data in edge_bundle.values()]
        value = min(values) if values else float("inf")
        return value * (3.2 if (u, v) in penalized_pairs else 1.0)

    return nx.shortest_path(graph, start, end, weight=weighted)


def _instructions(edges: list[tuple[Any, Any, Any, dict[str, Any]]]) -> list[dict[str, Any]]:
    if not edges:
        return []
    result: list[dict[str, Any]] = []
    previous_place: str | None = None
    labels = {
        "rijbaan": "de rijbaan",
        "verplicht fietspad": "het verplichte fietspad",
        "fiets/bromfietspad": "het fiets/bromfietspad",
        "pont": "de pont",
        "onzeker wegvak": "een onzeker wegvak",
    }
    for _, _, _, data in edges:
        decision: AccessDecision = data["_decision"]
        if decision.place != previous_place:
            if decision.place == "pont":
                text = "Neem de pont en zet de motor uit."
            elif previous_place:
                text = f"Wissel van {labels.get(previous_place, previous_place)} naar {labels.get(decision.place, decision.place)}."
            else:
                text = f"Start op {labels.get(decision.place, decision.place)}."
            result.append({"type": "transition", "text": text, "place": decision.place})
            previous_place = decision.place
    result.append({"type": "arrival", "text": "Bestemming bereikt.", "place": "aankomst"})
    return result


def _route_payload(
    graph: nx.MultiDiGraph,
    nodes: list[Any],
    weight: str,
    index: int,
) -> dict[str, Any]:
    edges = _choose_edges(graph, nodes, weight)
    features: list[dict[str, Any]] = []
    total_length = 0.0
    total_seconds = 0.0
    confidences: list[str] = []
    for u, v, key, data in edges:
        decision: AccessDecision = data["_decision"]
        length = float(data.get("length", 0.0) or 0.0)
        total_length += length
        total_seconds += float(data.get("travel_time", 0.0))
        confidences.append(decision.confidence)
        tags = {name: _stringify(data.get(name)) for name in TAG_KEYS if data.get(name) is not None}
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": _edge_coords(graph, u, v, data)},
            "properties": {
                "segment_id": f"{u}-{v}-{key}",
                "osm_id": str(data.get("osmid", f"{u}-{v}-{key}")),
                "name": _stringify(data.get("name")) or "Naamloos wegvak",
                "length_m": round(length),
                **decision.to_dict(),
                "osm_tags": tags,
            },
        })
    confidence = max(confidences, key=lambda item: CONFIDENCE_ORDER.get(item, 2)) if confidences else "laag"
    return {
        "id": str(uuid.uuid4()),
        "label": "Aanbevolen route" if index == 0 else f"Alternatief {index}",
        "distance_m": round(total_length),
        "duration_min": max(1, round(total_seconds / 60)),
        "confidence": confidence,
        "geometry": {"type": "FeatureCollection", "features": features},
        "instructions": _instructions(edges),
        "uncertain_segments": sum(1 for item in confidences if item == "laag"),
    }


def calculate_routes(
    start: tuple[float, float],
    end: tuple[float, float],
    settings: VehicleSettings,
    preference: str,
) -> dict[str, Any]:
    graph = network_manager.graph()
    legal = _build_legal_graph(graph, settings)
    weight = "quiet_cost" if preference == "quiet" else "travel_time"
    emission = evaluate_emission_access(settings.powertrain, settings.build_year, settings.exemption)
    if legal.number_of_edges() == 0:
        raise ValueError(emission.reason if not emission.allowed else "Het legale routenetwerk bevat geen bruikbare wegvakken.")
    start_node, start_snap = _nearest_node(legal, start[1], start[0], for_start=True)
    end_node, end_snap = _nearest_node(legal, end[1], end[0], for_start=False)

    try:
        paths: list[list[Any]] = []
        penalized_pairs: set[tuple[Any, Any]] = set()
        for _ in range(3):
            candidate = _path_with_penalties(legal, start_node, end_node, weight, penalized_pairs)
            if candidate in paths:
                break
            paths.append(candidate)
            penalized_pairs.update(zip(candidate, candidate[1:]))
        routes = [_route_payload(legal, path, weight, index) for index, path in enumerate(paths)]
        if routes:
            maximum = routes[0]["distance_m"] * 1.75
            routes = [route for route in routes if route["distance_m"] <= maximum]
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        reason = emission.reason if not emission.allowed else "Er is geen legaal verbonden pad gevonden voor dit voertuigprofiel."
        raise ValueError(reason) from None

    warnings: list[str] = []
    status = network_manager.status()
    if status["mode"] == "demo":
        warnings.append("Voorbeeldnetwerk actief: dit is een functionele demonstratie, geen routeadvies voor gebruik op straat.")
    if not emission.allowed:
        warnings.append(f"Uitstootvrije zone wordt vermeden: {emission.reason}")
    if start_snap > 1_500 or end_snap > 1_500:
        warnings.append("Een gekozen punt ligt ver van het actieve routenetwerk; controleer de aansluiting.")
    if any(route["uncertain_segments"] for route in routes):
        warnings.append("De route bevat wegvakken met lage betrouwbaarheid. Controleer de oranje segmenten ter plaatse.")

    return {
        "routes": routes,
        "warnings": warnings,
        "snapped": {
            "start": {"lat": graph.nodes[start_node]["y"], "lon": graph.nodes[start_node]["x"], "distance_m": round(start_snap)},
            "end": {"lat": graph.nodes[end_node]["y"], "lon": graph.nodes[end_node]["x"], "distance_m": round(end_snap)},
        },
        "data": status,
        "emission_decision": asdict(emission),
    }
