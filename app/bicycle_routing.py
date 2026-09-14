from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Any

import networkx as nx

from .bicycle_rules import BicycleDecision, evaluate_bicycle_access
from .data_manager import network_manager
from .routing import CONFIDENCE_ORDER, TAG_KEYS, _edge_coords, _nearest_node, _path_with_penalties, _stringify


@dataclass(frozen=True)
class BicycleSettings:
    bicycle_type: str = "regular"


_CACHE: dict[tuple[Any, str], nx.MultiDiGraph] = {}
_LOCK = threading.Lock()
HARD_BARRIERS = {"fence", "wall", "stile", "turnstile", "block"}


def _duration_seconds(value: Any) -> float | None:
    text = str(value or "")
    parts = text.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        return None
    return None


def _blocked_node(data: dict[str, Any]) -> bool:
    barrier = str(data.get("barrier", "")).lower()
    bicycle = str(data.get("bicycle", "")).lower()
    return barrier in HARD_BARRIERS and bicycle not in {"yes", "designated", "permissive"}


def _build_graph(graph: nx.MultiDiGraph, settings: BicycleSettings) -> nx.MultiDiGraph:
    key = (graph, settings.bicycle_type)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
    legal = nx.MultiDiGraph()
    legal.graph.update(graph.graph)
    legal.add_nodes_from(graph.nodes(data=True))
    for u, v, edge_key, raw in graph.edges(keys=True, data=True):
        if _blocked_node(graph.nodes[u]) or _blocked_node(graph.nodes[v]):
            continue
        data = dict(raw)
        decision = evaluate_bicycle_access(data, settings.bicycle_type)
        if not decision.allowed:
            continue
        length = max(float(data.get("length", 1.0) or 1.0), 1.0)
        seconds = _duration_seconds(data.get("duration")) if decision.place == "pont" else None
        seconds = seconds if seconds is not None else length / (max(decision.speed_kmh, 3) * 1000 / 3600)
        data["_bike_decision"] = decision
        data["bike_time"] = seconds
        data["bike_comfort"] = seconds * decision.comfort_factor
        legal.add_edge(u, v, key=edge_key, **data)

        oneway_bicycle = str(data.get("oneway:bicycle", "")).lower()
        oneway = str(data.get("oneway", "")).lower()
        if oneway_bicycle in {"no", "false", "0"} and oneway in {"yes", "true", "1", "-1"} and not graph.has_edge(v, u):
            reverse = dict(data)
            reverse["_bike_reverse"] = True
            legal.add_edge(v, u, key=f"bike-reverse-{edge_key}", **reverse)
    components = nx.strongly_connected_components(legal)
    legal.graph["_routing_nodes"] = max(components, key=len, default=set())
    with _LOCK:
        if len(_CACHE) > 3:
            _CACHE.clear()
        _CACHE[key] = legal
    return legal


def _chosen_edges(graph: nx.MultiDiGraph, nodes: list[Any], weight: str):
    for u, v in zip(nodes, nodes[1:]):
        bundle = graph.get_edge_data(u, v) or {}
        edge_key, data = min(bundle.items(), key=lambda item: float(item[1].get(weight, float("inf"))))
        yield u, v, edge_key, data


def _payload(graph: nx.MultiDiGraph, nodes: list[Any], weight: str, index: int) -> dict[str, Any]:
    features = []
    distance = seconds = 0.0
    confidences: list[str] = []
    instructions = []
    previous_place = None
    for u, v, edge_key, data in _chosen_edges(graph, nodes, weight):
        decision: BicycleDecision = data["_bike_decision"]
        length = float(data.get("length", 0) or 0)
        distance += length
        seconds += float(data.get("bike_time", 0) or 0)
        confidences.append(decision.confidence)
        if decision.place != previous_place:
            text = "Neem de pont met de fiets aan de hand." if decision.place == "pont" else (
                "Stap af en loop met de fiets aan de hand." if decision.dismount else f"Ga verder via {decision.place}."
            )
            instructions.append({"type": "transition", "text": text, "place": decision.place})
            previous_place = decision.place
        tags = {name: _stringify(data.get(name)) for name in TAG_KEYS if data.get(name) is not None}
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": _edge_coords(graph, u, v, data)},
            "properties": {
                "segment_id": f"bike-{u}-{v}-{edge_key}",
                "osm_id": str(data.get("osmid", f"{u}-{v}-{edge_key}")),
                "name": _stringify(data.get("name")) or "Naamloos wegvak",
                "length_m": round(length),
                "mode": "bike_walk" if decision.dismount else "bike",
                **decision.to_dict(),
                "osm_tags": tags,
            },
        })
    instructions.append({"type": "arrival", "text": "Bestemming bereikt.", "place": "aankomst"})
    confidence = max(confidences, key=lambda item: CONFIDENCE_ORDER.get(item, 2)) if confidences else "laag"
    return {
        "id": str(uuid.uuid4()),
        "label": "Aanbevolen fietsroute" if index == 0 else f"Fietsalternatief {index}",
        "distance_m": round(distance),
        "duration_min": max(1, round(seconds / 60)),
        "confidence": confidence,
        "geometry": {"type": "FeatureCollection", "features": features},
        "instructions": instructions,
        "uncertain_segments": sum(c == "laag" for c in confidences),
    }


def calculate_bicycle_routes(start: tuple[float, float], end: tuple[float, float], settings: BicycleSettings, preference: str) -> dict[str, Any]:
    graph = network_manager.graph()
    legal = _build_graph(graph, settings)
    if not legal.edges:
        raise ValueError("Het fietsnetwerk bevat geen bruikbare wegvakken.")
    start_node, start_snap = _nearest_node(legal, start[1], start[0], for_start=True)
    end_node, end_snap = _nearest_node(legal, end[1], end[0], for_start=False)
    weight = "bike_comfort" if preference in {"quiet", "comfortable"} else "bike_time"
    try:
        paths = []
        penalties: set[tuple[Any, Any]] = set()
        for _ in range(3):
            path = _path_with_penalties(legal, start_node, end_node, weight, penalties)
            if path in paths:
                break
            paths.append(path)
            penalties.update(zip(path, path[1:]))
        routes = [_payload(legal, path, weight, i) for i, path in enumerate(paths)]
        if routes:
            routes = [r for r in routes if r["distance_m"] <= routes[0]["distance_m"] * 1.75]
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        raise ValueError("Er is geen verbonden fietsroute gevonden.") from None
    warnings = []
    if network_manager.status()["mode"] == "demo":
        warnings.append("Voorbeeldnetwerk actief: gebruik deze fietsroute niet als straatadvies.")
    if start_snap > 1500 or end_snap > 1500:
        warnings.append("Een gekozen punt ligt ver van het actieve fietsnetwerk.")
    if any(r["uncertain_segments"] for r in routes):
        warnings.append("Oranje fietsdelen hebben onvolledige OSM-toegangstags; controleer de borden.")
    return {
        "routes": routes,
        "warnings": warnings,
        "snapped": {
            "start": {"lat": graph.nodes[start_node]["y"], "lon": graph.nodes[start_node]["x"], "distance_m": round(start_snap)},
            "end": {"lat": graph.nodes[end_node]["y"], "lon": graph.nodes[end_node]["x"], "distance_m": round(end_snap)},
        },
        "data": network_manager.status(),
    }
