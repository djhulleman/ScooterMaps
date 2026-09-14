from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .bicycle_routing import BicycleSettings, calculate_bicycle_routes
from .transit_data import StopGroup, haversine_m, transit_data
from .transit_rules import AMSTERDAM_TZ, bicycle_onboard_decision, ensure_amsterdam, load_transit_rules


ROOT = Path(__file__).resolve().parents[1]
PARKING_PATH = ROOT / "data" / "bicycle_parking.json"


@dataclass(frozen=True)
class JourneySettings:
    mode: str = "bike_all"
    bicycle_type: str = "regular"
    preference: str = "fast"
    max_access_bike_km: float = 6.0
    max_egress_bike_km: float = 6.0
    max_transfers: int = 1
    secure_parking: bool = True
    average_speed_kmh: float | None = None
    boarding_buffer_min: float | None = None
    transfer_buffer_min: float | None = None
    exit_buffer_min: float | None = None
    allowed_modes: frozenset[str] = frozenset({"train", "metro", "tram", "bus", "ferry"})
    cost_weights: dict[str, float] = field(default_factory=dict)


def _iso(value: datetime) -> str:
    return ensure_amsterdam(value).isoformat()


def _line(coords: list[list[float]]) -> dict[str, Any]:
    return {"type": "LineString", "coordinates": coords}


def _walk_segment(start: tuple[float, float], end: tuple[float, float], label: str) -> dict[str, Any]:
    distance = haversine_m(start, end)
    minutes = max(1, round(distance / (4.8 * 1000 / 60)))
    return {
        "id": str(uuid.uuid4()), "mode": "walk", "label": label,
        "distance_m": round(distance), "duration_min": minutes,
        "geometry": _line([[start[1], start[0]], [end[1], end[0]]]),
        "details": "Loopverbinding is een rechte lokale benadering; volg ter plaatse de voetgangersroute.",
    }


def _bike_segments(start: tuple[float, float], end: tuple[float, float], settings: JourneySettings) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    payload = calculate_bicycle_routes(start, end, BicycleSettings(settings.bicycle_type), settings.preference)
    route = payload["routes"][0]
    total_length = max(route["distance_m"], 1)
    if settings.average_speed_kmh:
        default_speed = 22.0 if settings.bicycle_type == "ebike" else 16.0
        route = dict(route)
        route["duration_min"] = max(1, round(route["duration_min"] * default_speed / settings.average_speed_kmh))
    segments = []
    for feature in route["geometry"]["features"]:
        props = feature["properties"]
        duration = max(1, round(route["duration_min"] * props["length_m"] / total_length))
        segments.append({
            "id": props["segment_id"], "mode": props.get("mode", "bike"),
            "label": props["name"], "distance_m": props["length_m"], "duration_min": duration,
            "geometry": feature["geometry"], "details": props["reason"],
            "confidence": props["confidence"], "place": props["place"], "osm_tags": props.get("osm_tags", {}),
        })
    return segments, route, payload.get("warnings", [])


def _straight_bike_segment(start: tuple[float, float], end: tuple[float, float], speed_kmh: float, label: str) -> dict[str, Any]:
    distance = haversine_m(start, end)
    return {
        "id": str(uuid.uuid4()), "mode": "bike", "label": label,
        "distance_m": round(distance), "duration_min": max(1, round(distance / (speed_kmh * 1000 / 60))),
        "geometry": _line([[start[1], start[0]], [end[1], end[0]]]),
        "details": "Rechte benadering omdat voor dit korte aansluitdeel geen verbonden fietsroute beschikbaar was.",
        "confidence": "laag", "place": "benadering",
    }


def _access_bike(start: tuple[float, float], end: tuple[float, float], settings: JourneySettings, label: str) -> tuple[list[dict[str, Any]], int, int, list[str]]:
    speed = settings.average_speed_kmh or (22.0 if settings.bicycle_type == "ebike" else 16.0)
    try:
        segments, route, warnings = _bike_segments(start, end, settings)
        for segment in segments:
            segment["label"] = f"{label} · {segment['label']}"
        return segments, int(route["distance_m"]), int(route["duration_min"]), warnings
    except ValueError:
        fallback = _straight_bike_segment(start, end, speed, label)
        return [fallback], fallback["distance_m"], fallback["duration_min"], [fallback["details"]]


def _parking_candidates() -> list[dict[str, Any]]:
    rules = load_transit_rules()
    candidates = list(rules.get("parking_fallback", []))
    if PARKING_PATH.exists():
        try:
            payload = json.loads(PARKING_PATH.read_text(encoding="utf-8"))
            items = payload.get("features", payload) if isinstance(payload, dict) else payload
            for item in items:
                if item.get("geometry"):
                    lon, lat = item["geometry"]["coordinates"][:2]
                    props = item.get("properties", {})
                    candidates.append({"id": props.get("id", str(uuid.uuid4())), "name": props.get("name", "Fietsparkeren"), "lat": lat, "lon": lon, **props})
                elif "lat" in item and "lon" in item:
                    candidates.append(item)
        except (OSError, ValueError, TypeError):
            pass
    return candidates


def _select_parking(station: StopGroup, secure_preferred: bool) -> dict[str, Any]:
    candidates = []
    for parking in _parking_candidates():
        if str(parking.get("access", "")).lower() in {"private", "no"}:
            continue
        distance = haversine_m((station.lat, station.lon), (float(parking["lat"]), float(parking["lon"])))
        if distance <= 600:
            score = distance
            if secure_preferred:
                score += -140 if parking.get("secure") else 180 if parking.get("secure") is False else 60
                score += -60 if parking.get("covered") else 25
            capacity = parking.get("capacity")
            if isinstance(capacity, (int, float)):
                score -= min(float(capacity), 1000) / 12
            if parking.get("opening_hours") in {None, "", "onbekend"}:
                score += 20
            candidates.append((score, distance, parking))
    if not candidates:
        return {
            "id": f"unknown-{station.id}", "name": f"Fietsparkeren bij {station.name}",
            "lat": station.lat, "lon": station.lon, "distance_to_stop_m": 0,
            "capacity": None, "covered": None, "secure": None, "opening_hours": "onbekend", "confidence": "laag",
        }
    _, distance, best = min(candidates, key=lambda item: item[0])
    return {**best, "distance_to_stop_m": round(distance)}


def _find_transit(
    origin: tuple[float, float], destination: tuple[float, float], when: datetime,
    settings: JourneySettings, *, access_mode: str, egress_mode: str, bike_onboard: bool,
    arrive_by: bool = False, ov_fiets: bool = False,
) -> dict[str, Any] | None:
    speed = settings.average_speed_kmh or (22.0 if settings.bicycle_type == "ebike" else 16.0)
    max_access = settings.max_access_bike_km * 1000 if access_mode == "bike" else 1800
    max_egress = settings.max_egress_bike_km * 1000 if egress_mode == "bike" else 1800
    station_names = set(load_transit_rules()["ov_fiets"]["stations"]) if ov_fiets else None

    common = dict(
        access_mode=access_mode, egress_mode=egress_mode,
        bicycle_onboard=bike_onboard, max_access_m=max_access, max_egress_m=max_egress,
        max_transfers=settings.max_transfers, bicycle_speed_kmh=speed,
        egress_group_names=station_names, allowed_modes=set(settings.allowed_modes),
        boarding_buffer_min=settings.boarding_buffer_min,
        transfer_buffer_min=settings.transfer_buffer_min,
        exit_buffer_min=settings.exit_buffer_min,
    )

    def run(departure: datetime):
        return transit_data.find_journey(origin, destination, departure, **common)

    if not arrive_by:
        return run(when)
    return transit_data.find_journey_arrive_by(origin, destination, when, **common)


def _transit_option(
    journey: dict[str, Any], origin: tuple[float, float], destination: tuple[float, float],
    settings: JourneySettings, option_type: str,
) -> dict[str, Any]:
    access_group: StopGroup = journey["access"]["group"]
    egress_group: StopGroup = journey["egress"]["group"]
    access_point = (access_group.lat, access_group.lon)
    egress_point = (egress_group.lat, egress_group.lon)
    segments: list[dict[str, Any]] = []
    warnings = list(journey["warnings"])
    bike_distance = 0
    bike_minutes = 0
    walk_minutes = 0
    parking = None

    if option_type in {"bike_transit", "park_transit"}:
        if option_type == "park_transit":
            parking = _select_parking(access_group, settings.secure_parking)
            bike_end = (float(parking["lat"]), float(parking["lon"]))
        else:
            bike_end = access_point
        bike, distance, minutes, bike_warnings = _access_bike(origin, bike_end, settings, "Naar het OV")
        segments.extend(bike)
        bike_distance += distance
        bike_minutes += minutes
        warnings.extend(bike_warnings)
        if parking:
            walk = _walk_segment(bike_end, access_point, f"Loop naar {access_group.name}")
            segments.append(walk)
            walk_minutes += walk["duration_min"]
            if parking.get("capacity") is None:
                warnings.append("Capaciteit van de gekozen fietsenstalling is onbekend; controleer ter plaatse of er plek is.")
            if parking.get("opening_hours") in {None, "", "onbekend"}:
                warnings.append("Openingstijden van de fietsenstalling zijn niet bevestigd.")
    else:
        walk = _walk_segment(origin, access_point, f"Loop naar {access_group.name}")
        segments.append(walk)
        walk_minutes += walk["duration_min"]

    for ride in journey["rides"]:
        segments.append({
            "id": f"transit-{ride['trip_id']}-{ride['from_group']}", "mode": ride["mode"],
            "label": f"{ride['mode'].title()} {ride['line']} richting {ride['direction'] or 'eindpunt'}",
            "from": ride["from_name"], "to": ride["to_name"],
            "departure": ride["departure"], "arrival": ride["arrival"],
            "platforms": ride["platforms"], "geometry": _line(ride["coordinates"]),
            "details": ride.get("bike_rule") or "Dienstregeling uit de lokale GTFS-cache.",
            "color": ride.get("color"), "rule_source": ride.get("rule_source"),
        })

    if option_type == "bike_transit":
        bike, distance, minutes, bike_warnings = _access_bike(egress_point, destination, settings, "Vanaf het OV")
        segments.extend(bike)
        bike_distance += distance
        bike_minutes += minutes
        warnings.extend(bike_warnings)
    elif option_type == "ov_fiets":
        bike, distance, minutes, bike_warnings = _access_bike(egress_point, destination, settings, "Met OV-fiets")
        segments.extend(bike)
        bike_distance += distance
        bike_minutes += minutes
        warnings.extend(bike_warnings)
        warnings.append("OV-fiets moet bij dezelfde huurlocatie worden teruggebracht; actuele beschikbaarheid is niet gegarandeerd.")
    else:
        walk = _walk_segment(egress_point, destination, "Laatste stuk lopen")
        segments.append(walk)
        walk_minutes += walk["duration_min"]

    labels = {
        "ov_only": "Hele route met OV", "bike_transit": "Fiets mee in OV",
        "park_transit": "Fiets parkeren + OV", "ov_fiets": "OV + OV-fiets",
        "return_to_bike": "Terug naar geparkeerde fiets",
    }
    start_dt = datetime.fromisoformat(journey["departure"])
    transit_arrival = datetime.fromisoformat(journey["arrival"])
    # Replace radial estimates by the routed bicycle/walk durations where possible.
    estimated = (transit_arrival - start_dt).total_seconds() / 60
    access_adjust = bike_minutes + walk_minutes - journey["access_duration_min"] - journey["egress_duration_min"]
    total_minutes = max(1, round(estimated + access_adjust))
    arrival = start_dt + timedelta(minutes=total_minutes)
    tickets = list(journey["tickets"])
    supplement = float(journey["supplement_eur"])
    if option_type == "ov_fiets":
        product = load_transit_rules()["ov_fiets"]
        tickets.append({"name": product["ticket_name"], "price_eur": product["price_per_24h_eur"]})
        supplement += float(product["price_per_24h_eur"])
    return {
        "id": str(uuid.uuid4()), "type": option_type, "label": labels[option_type],
        "departure": _iso(start_dt), "arrival": _iso(arrival), "duration_min": total_minutes,
        "bike_distance_m": bike_distance, "bike_duration_min": bike_minutes,
        "walk_duration_min": walk_minutes, "wait_duration_min": journey["wait_duration_min"],
        "buffer_duration_min": journey.get("buffer_duration_min", 0),
        "transit_duration_min": journey["transit_duration_min"], "transfers": journey["transfers"],
        "supplement_eur": round(supplement, 2), "tickets": tickets,
        "base_fare_known": False, "cost_note": "Regulier OV-tarief niet berekend; alleen fietsproducten zijn opgeteld.",
        "confidence": "middel", "warnings": list(dict.fromkeys(warnings)), "segments": segments,
        "parking": parking,
    }


def _bike_only(origin: tuple[float, float], destination: tuple[float, float], when: datetime, settings: JourneySettings, arrive_by: bool = False) -> dict[str, Any]:
    segments, route, warnings = _bike_segments(origin, destination, settings)
    departure = when - timedelta(minutes=route["duration_min"]) if arrive_by else when
    arrival = when if arrive_by else when + timedelta(minutes=route["duration_min"])
    return {
        "id": route["id"], "type": "bike_all", "label": "Hele route fietsen",
        "departure": _iso(departure), "arrival": _iso(arrival), "duration_min": route["duration_min"],
        "bike_distance_m": route["distance_m"], "bike_duration_min": route["duration_min"],
        "walk_duration_min": 0, "wait_duration_min": 0, "buffer_duration_min": 0, "transit_duration_min": 0,
        "transfers": 0, "supplement_eur": 0.0, "tickets": [], "base_fare_known": True,
        "cost_note": "Geen OV-kosten.", "confidence": route["confidence"], "warnings": warnings,
        "segments": segments, "parking": None,
    }


def _score(option: dict[str, Any], settings: JourneySettings) -> float:
    weights = dict(load_transit_rules()["routing"]["cost_weights"])
    weights.update(settings.cost_weights)
    return round(
        option["duration_min"] * weights["duration_min"]
        + option["bike_distance_m"] / 1000 * weights["bike_km"]
        + option["walk_duration_min"] * weights["walk_min"]
        + option["transfers"] * weights["transfer"]
        + option["supplement_eur"] * weights["supplement_eur"],
        2,
    )


def plan_journeys(
    origin: tuple[float, float], destination: tuple[float, float], when: datetime,
    settings: JourneySettings, *, arrive_by: bool = False,
) -> dict[str, Any]:
    when = ensure_amsterdam(when)
    requested = [settings.mode]
    if settings.mode == "compare":
        requested = ["bike_all", "ov_only", "bike_transit", "park_transit", "ov_fiets"]
    options = []
    unavailable = []
    for mode in requested:
        try:
            if mode == "bike_all":
                options.append(_bike_only(origin, destination, when, settings, arrive_by))
                continue
            mapping = {
                "ov_only": ("walk", "walk", False, False),
                "return_to_bike": ("walk", "walk", False, False),
                "bike_transit": ("bike", "bike", True, False),
                "park_transit": ("bike", "walk", False, False),
                "ov_fiets": ("walk", "bike", False, True),
            }
            access_mode, egress_mode, onboard, ov_fiets = mapping[mode]
            journey = _find_transit(origin, destination, when, settings, access_mode=access_mode, egress_mode=egress_mode, bike_onboard=onboard, arrive_by=arrive_by, ov_fiets=ov_fiets)
            if not journey:
                if mode == "bike_transit":
                    probe_end = when + timedelta(minutes=1)
                    gvb = bicycle_onboard_decision("GVB", 1, "52", "", when, probe_end)
                    ns = bicycle_onboard_decision("IFF:NS", 2, "Intercity", "Intercity", when, probe_end)
                    if not gvb.allowed and not ns.allowed:
                        reason = "de voertuigen rijden wel, maar een gewone fiets mag tijdens deze spits niet mee; dit is geen dienstregelingswachttijd"
                    else:
                        reason = "geen praktische verbinding met maximaal 30 minuten echte dienstregelingswachttijd"
                else:
                    reason = "geen passende verbinding binnen de ingestelde afstanden en dienstregeling"
                unavailable.append(f"{mode}: {reason}")
                continue
            options.append(_transit_option(journey, origin, destination, settings, mode))
        except ValueError as exc:
            unavailable.append(f"{mode}: {exc}")
    if not options:
        detail = "; ".join(unavailable) or "Geen route gevonden."
        raise ValueError(detail)
    for option in options:
        option["score"] = _score(option, settings)
    options.sort(key=lambda option: option["score"])
    for index, option in enumerate(options):
        option["recommended"] = index == 0
    return {
        "options": options,
        "warnings": unavailable,
        "query": {"mode": settings.mode, "time": _iso(when), "arrive_by": arrive_by, "timezone": "Europe/Amsterdam"},
        "data": {"network": calculate_network_status(), "transit": transit_data.status()},
        "disclaimer": "Dienstregeling en regels zijn lokaal gecachet. Verkeersborden, personeel, storingen en beschikbare fietsplaatsen blijven leidend.",
    }


def calculate_network_status() -> dict[str, Any]:
    from .data_manager import network_manager
    return network_manager.status()
