from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from .transit_rules import AMSTERDAM_TZ, bicycle_onboard_decision, ensure_amsterdam, load_transit_rules


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "gtfs" / "amsterdam-transit.sqlite"
MODE_NAMES = {0: "tram", 1: "metro", 2: "trein", 3: "bus", 4: "pont"}


def gtfs_time_to_seconds(value: str) -> int:
    """Parse GTFS times, including service-day values beyond 24:00."""
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    if minutes not in range(60) or seconds not in range(60) or hours < 0:
        raise ValueError(f"Ongeldige GTFS-tijd: {value}")
    return hours * 3600 + minutes * 60 + seconds


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    value = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 6_371_000 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _access_seconds(distance_m: float, speed_kmh: float, mode: str) -> float:
    # Straight-line distance underestimates Amsterdam street distance (water,
    # railways and station entrances). Conservative factors protect transfers
    # and arrive-by results until the chosen leg is routed on the street graph.
    detour = 1.35 if mode == "bike" else 1.20
    return distance_m * detour / (speed_kmh * 1000 / 3600)


def _display_name(value: str) -> str:
    bracketed = re.match(r"^\[([^]]+)]", value)
    if bracketed:
        return bracketed.group(1).strip()
    return re.sub(r"^OV halte,?\s*", "", value).strip() or value


def _station_name_key(value: str) -> str:
    value = re.sub(r"\bstation\b|station$", " ", value.lower())
    return re.sub(r"[^a-z0-9]+", "", value)


@dataclass(frozen=True)
class StopGroup:
    id: str
    name: str
    lat: float
    lon: float
    members: tuple[str, ...]
    platforms: tuple[str, ...]


class TransitData:
    def __init__(self, path: Path = DEFAULT_DB_PATH) -> None:
        self.path = path
        self._stamp = 0
        self._stops: dict[str, dict[str, Any]] = {}
        self._stop_groups: dict[str, str] = {}
        self._groups: dict[str, StopGroup] = {}
        self._load_stops()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _load_stops(self) -> None:
        if not self.path.exists():
            return
        stamp = self.path.stat().st_mtime_ns
        if self._stops and stamp == self._stamp:
            return
        try:
            with self._connect() as db:
                rows = list(db.execute("SELECT * FROM stops"))
        except (sqlite3.Error, OSError):
            return
        self._stamp = stamp
        self._stops = {row["stop_id"]: dict(row) for row in rows}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for stop in self._stops.values():
            group_id = stop["parent_station"] or stop["stop_id"]
            self._stop_groups[stop["stop_id"]] = group_id
            grouped.setdefault(group_id, []).append(stop)
        groups = {}
        for group_id, members in grouped.items():
            names = [_display_name(str(item["name"])) for item in members]
            counts = Counter(names)
            preferred = min(counts, key=lambda name: (-counts[name], len(name), name))
            groups[group_id] = StopGroup(
                group_id,
                preferred,
                sum(float(item["lat"]) for item in members) / len(members),
                sum(float(item["lon"]) for item in members) / len(members),
                tuple(str(item["stop_id"]) for item in members),
                tuple(sorted({str(item["platform_code"]) for item in members if item["platform_code"]})),
            )
        self._groups = groups
        self._connections_for_day.cache_clear()

    def status(self) -> dict[str, Any]:
        self._load_stops()
        if not self.path.exists() or not self._groups:
            return {
                "state": "missing",
                "message": "Geen lokale OV-cache. Voer tools\\refresh_transit_data.py uit.",
                "path": str(self.path),
            }
        try:
            with self._connect() as db:
                meta = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT key, value FROM meta")}
            return {"state": "ready", "message": "Lokale regionale GTFS-cache gereed.", "path": str(self.path), **meta}
        except (sqlite3.Error, ValueError) as exc:
            return {"state": "invalid", "message": f"OV-cache is ongeldig: {exc}", "path": str(self.path)}

    @staticmethod
    def _validate_query_day(day: date, status: dict[str, Any]) -> None:
        try:
            first = date.fromisoformat(str(status["window_start"]))
            last = date.fromisoformat(str(status["window_end"]))
        except (KeyError, ValueError):
            raise ValueError("OV-cache mist een geldige datumperiode; vernieuw de GTFS-cache.") from None
        if not first <= day <= last:
            raise ValueError(f"De lokale OV-cache geldt van {first:%d-%m-%Y} t/m {last:%d-%m-%Y}; vernieuw de dienstregeling.")

    def nearest_groups(self, point: tuple[float, float], max_distance_m: float, limit: int = 16, names: set[str] | None = None) -> list[tuple[StopGroup, float]]:
        self._load_stops()
        accepted = []
        for group in self._groups.values():
            if names and not any(
                _station_name_key(name) in _station_name_key(group.name)
                or _station_name_key(group.name) in _station_name_key(name)
                for name in names
            ):
                continue
            distance = haversine_m(point, (group.lat, group.lon))
            if distance <= max_distance_m:
                accepted.append((group, distance))
        accepted.sort(key=lambda item: item[1])
        return accepted[:limit]

    def _active_services(self, day: date) -> set[str]:
        with self._connect() as db:
            rows = db.execute("SELECT service_id, exception_type FROM service_dates WHERE date = ?", (day.isoformat(),))
            active: set[str] = set()
            for row in rows:
                if int(row["exception_type"]) == 1:
                    active.add(str(row["service_id"]))
                else:
                    active.discard(str(row["service_id"]))
            return active

    @lru_cache(maxsize=5)
    def _connections_for_day(self, day: date) -> tuple[dict[str, Any], ...]:
        services = self._active_services(day)
        if not services:
            return ()
        result = []
        midnight = datetime.combine(day, datetime.min.time(), tzinfo=AMSTERDAM_TZ)
        with self._connect() as db:
            placeholders = ",".join("?" for _ in services)
            query = f"""
                SELECT st.trip_id, st.stop_sequence, st.stop_id, st.arrival_secs, st.departure_secs,
                       st.pickup_type, st.drop_off_type, t.headsign, t.product_name, t.bikes_allowed,
                       r.route_id, r.agency_id, r.short_name, r.long_name, r.route_type, r.color
                FROM stop_times st
                JOIN trips t ON t.trip_id = st.trip_id
                JOIN routes r ON r.route_id = t.route_id
                WHERE t.service_id IN ({placeholders})
                ORDER BY st.trip_id, st.stop_sequence
            """
            previous = None
            for row in db.execute(query, tuple(services)):
                current = dict(row)
                if previous and previous["trip_id"] == current["trip_id"]:
                    from_group = self._stop_groups.get(previous["stop_id"])
                    to_group = self._stop_groups.get(current["stop_id"])
                    if from_group and to_group and from_group != to_group:
                        departure = midnight + timedelta(seconds=int(previous["departure_secs"]))
                        arrival = midnight + timedelta(seconds=int(current["arrival_secs"]))
                        if arrival < departure:
                            arrival += timedelta(days=1)
                        result.append({
                            "trip_id": current["trip_id"], "route_id": current["route_id"],
                            "agency_id": current["agency_id"], "route_type": int(current["route_type"]),
                            "short_name": current["short_name"] or "", "long_name": current["long_name"] or "",
                            "headsign": current["headsign"] or "", "product_name": current["product_name"] or "",
                            "from_group": from_group, "to_group": to_group,
                            "departure": departure, "arrival": arrival,
                            "pickup_type": int(previous["pickup_type"] or 0),
                            "drop_off_type": int(current["drop_off_type"] or 0),
                            "color": current["color"] or "",
                        })
                previous = current
        result.sort(key=lambda item: item["departure"])
        return tuple(result)

    def connections_around(self, moment: datetime) -> list[dict[str, Any]]:
        moment = ensure_amsterdam(moment)
        days = (moment.date() - timedelta(days=1), moment.date(), moment.date() + timedelta(days=1))
        connections = [dict(item) for day in days for item in self._connections_for_day(day)]
        connections.sort(key=lambda item: item["departure"])
        return connections

    def find_journey(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure: datetime,
        *,
        access_mode: str,
        egress_mode: str,
        bicycle_onboard: bool,
        max_access_m: float,
        max_egress_m: float,
        max_transfers: int,
        bicycle_speed_kmh: float,
        egress_group_names: set[str] | None = None,
        allowed_modes: set[str] | None = None,
        boarding_buffer_min: float | None = None,
        transfer_buffer_min: float | None = None,
        exit_buffer_min: float | None = None,
    ) -> dict[str, Any] | None:
        status = self.status()
        if status["state"] != "ready":
            raise ValueError(status["message"])
        departure = ensure_amsterdam(departure)
        self._validate_query_day(departure.date(), status)
        rules = load_transit_rules()["routing"]
        walk_speed = float(rules["walk_speed_kmh"])
        access_speed = bicycle_speed_kmh if access_mode == "bike" else walk_speed
        egress_speed = bicycle_speed_kmh if egress_mode == "bike" else walk_speed
        # A single interchange has many platform/stop-area identifiers in GTFS-NL.
        # Keep a broad candidate set so a nearby tram stop cannot crowd out a
        # slightly farther metro or railway station.
        origins = self.nearest_groups(origin, max_access_m, limit=80)
        destinations = self.nearest_groups(destination, max_egress_m, limit=80, names=egress_group_names)
        if not origins or not destinations:
            return None

        access = {
            group.id: {"group": group, "distance_m": distance, "duration_s": _access_seconds(distance, access_speed, access_mode)}
            for group, distance in origins
        }
        egress = {
            group.id: {"group": group, "distance_m": distance, "duration_s": _access_seconds(distance, egress_speed, egress_mode)}
            for group, distance in destinations
        }
        board_buffer = float(boarding_buffer_min if boarding_buffer_min is not None else rules["boarding_buffer_min"]) * 60
        transfer_buffer = float(transfer_buffer_min if transfer_buffer_min is not None else rules["transfer_buffer_min"]) * 60
        exit_buffer = float(exit_buffer_min if exit_buffer_min is not None else rules["exit_buffer_min"]) * 60
        max_boardings = max_transfers + 1
        labels: list[dict[str, dict[str, Any]]] = [dict() for _ in range(max_boardings + 1)]
        for group_id, item in access.items():
            labels[0][group_id] = {
                "arrival": departure.timestamp() + item["duration_s"] + board_buffer,
                "rides": [], "access_group": group_id,
            }
        trip_states: dict[tuple[str, int], dict[str, Any]] = {}
        horizon = departure + timedelta(hours=6)
        for connection in self.connections_around(departure):
            dep = connection["departure"]
            arr = connection["arrival"]
            if dep < departure or dep > horizon:
                continue
            if allowed_modes is not None and MODE_NAMES.get(connection["route_type"], "ov") not in allowed_modes:
                continue
            for boardings in range(1, max_boardings + 1):
                trip_key = (connection["trip_id"], boardings)
                onboard = trip_states.get(trip_key)
                if onboard is None:
                    previous = labels[boardings - 1].get(connection["from_group"])
                    buffer = 0 if boardings == 1 else transfer_buffer
                    if not previous or previous["arrival"] + buffer > dep.timestamp() or connection["pickup_type"] == 1:
                        continue
                    decision = None
                    if bicycle_onboard:
                        decision = bicycle_onboard_decision(
                            connection["agency_id"], connection["route_type"], connection["short_name"],
                            connection["product_name"] or connection["long_name"], dep, arr,
                        )
                        if not decision.allowed:
                            continue
                    rides = [dict(item) for item in previous["rides"]]
                    rides.append(self._new_ride(connection, decision))
                    onboard = {
                        "arrival": arr.timestamp(), "rides": rides,
                        "access_group": previous["access_group"], "current_group": connection["to_group"],
                    }
                    trip_states[trip_key] = onboard
                else:
                    if onboard["current_group"] != connection["from_group"]:
                        continue
                    decision = None
                    if bicycle_onboard:
                        decision = bicycle_onboard_decision(
                            connection["agency_id"], connection["route_type"], connection["short_name"],
                            connection["product_name"] or connection["long_name"], dep, arr,
                        )
                        if not decision.allowed:
                            trip_states.pop(trip_key, None)
                            continue
                    rides = [dict(item) for item in onboard["rides"]]
                    rides[-1] = dict(rides[-1])
                    rides[-1]["to_group"] = connection["to_group"]
                    rides[-1]["to_name"] = self._groups[connection["to_group"]].name
                    rides[-1]["arrival"] = arr.isoformat()
                    rides[-1]["coordinates"] = [*rides[-1]["coordinates"], [self._groups[connection["to_group"]].lon, self._groups[connection["to_group"]].lat]]
                    onboard = {**onboard, "arrival": arr.timestamp(), "rides": rides, "current_group": connection["to_group"]}
                    trip_states[trip_key] = onboard
                if connection["drop_off_type"] != 1:
                    old = labels[boardings].get(connection["to_group"])
                    if not old or onboard["arrival"] < old["arrival"]:
                        labels[boardings][connection["to_group"]] = onboard

        choices = []
        max_wait_seconds = float(rules.get("max_wait_min", 30)) * 60
        for boardings in range(1, max_boardings + 1):
            for group_id, end_info in egress.items():
                state = labels[boardings].get(group_id)
                if not state:
                    continue
                access_info = access[state["access_group"]]
                first_board_ts = datetime.fromisoformat(state["rides"][0]["departure"]).timestamp()
                practical_wait = first_board_ts - departure.timestamp() - access_info["duration_s"] - board_buffer
                if practical_wait > max_wait_seconds:
                    continue
                final_arrival = state["arrival"] + exit_buffer + end_info["duration_s"]
                choices.append((final_arrival, boardings, group_id, state))
        if not choices:
            return None
        final_arrival, boardings, end_group, state = min(choices, key=lambda item: item[0])
        access_info = access[state["access_group"]]
        egress_info = egress[end_group]
        rides = state["rides"]
        ticket_map = {}
        warnings = []
        for ride in rides:
            if ride.get("ticket_name"):
                ticket_map[ride["ticket_name"]] = float(ride.get("supplement_eur", 0))
            if ride.get("capacity_warning") and ride["capacity_warning"] not in warnings:
                warnings.append(ride["capacity_warning"])
        first_board = datetime.fromisoformat(rides[0]["departure"])
        last_arrival = datetime.fromisoformat(rides[-1]["arrival"])
        wait_s = max(0, (first_board - departure).total_seconds() - access_info["duration_s"] - board_buffer)
        return {
            "departure": departure.isoformat(),
            "arrival": datetime.fromtimestamp(final_arrival, AMSTERDAM_TZ).isoformat(),
            "access": access_info,
            "egress": egress_info,
            "rides": rides,
            "transfers": boardings - 1,
            "wait_duration_min": round(wait_s / 60),
            "buffer_duration_min": round((board_buffer + (boardings - 1) * transfer_buffer + exit_buffer) / 60),
            "transit_duration_min": max(1, round((last_arrival - first_board).total_seconds() / 60)),
            "access_duration_min": max(1, round(access_info["duration_s"] / 60)),
            "egress_duration_min": max(1, round((egress_info["duration_s"] + exit_buffer) / 60)),
            "supplement_eur": round(sum(ticket_map.values()), 2),
            "tickets": [{"name": name, "price_eur": price} for name, price in ticket_map.items()],
            "warnings": warnings,
        }

    def find_journey_arrive_by(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        arrival_by: datetime,
        *,
        access_mode: str,
        egress_mode: str,
        bicycle_onboard: bool,
        max_access_m: float,
        max_egress_m: float,
        max_transfers: int,
        bicycle_speed_kmh: float,
        egress_group_names: set[str] | None = None,
        allowed_modes: set[str] | None = None,
        boarding_buffer_min: float | None = None,
        transfer_buffer_min: float | None = None,
        exit_buffer_min: float | None = None,
    ) -> dict[str, Any] | None:
        """Reverse connection scan that maximizes the door-to-door departure time."""
        status = self.status()
        if status["state"] != "ready":
            raise ValueError(status["message"])
        arrival_by = ensure_amsterdam(arrival_by)
        self._validate_query_day(arrival_by.date(), status)
        rules = load_transit_rules()["routing"]
        walk_speed = float(rules["walk_speed_kmh"])
        access_speed = bicycle_speed_kmh if access_mode == "bike" else walk_speed
        egress_speed = bicycle_speed_kmh if egress_mode == "bike" else walk_speed
        origins = self.nearest_groups(origin, max_access_m, limit=80)
        destinations = self.nearest_groups(destination, max_egress_m, limit=80, names=egress_group_names)
        if not origins or not destinations:
            return None
        access = {
            group.id: {"group": group, "distance_m": distance, "duration_s": _access_seconds(distance, access_speed, access_mode)}
            for group, distance in origins
        }
        egress = {
            group.id: {"group": group, "distance_m": distance, "duration_s": _access_seconds(distance, egress_speed, egress_mode)}
            for group, distance in destinations
        }
        board_buffer = float(boarding_buffer_min if boarding_buffer_min is not None else rules["boarding_buffer_min"]) * 60
        transfer_buffer = float(transfer_buffer_min if transfer_buffer_min is not None else rules["transfer_buffer_min"]) * 60
        exit_buffer = float(exit_buffer_min if exit_buffer_min is not None else rules["exit_buffer_min"]) * 60
        max_boardings = max_transfers + 1
        labels: list[dict[str, dict[str, Any]]] = [dict() for _ in range(max_boardings + 1)]
        for group_id, item in egress.items():
            labels[0][group_id] = {
                "departure": arrival_by.timestamp() - item["duration_s"] - exit_buffer,
                "rides": [], "egress_group": group_id,
            }
        trip_states: dict[tuple[str, int], dict[str, Any]] = {}
        horizon_start = arrival_by - timedelta(hours=6)
        for connection in reversed(self.connections_around(horizon_start)):
            dep = connection["departure"]
            arr = connection["arrival"]
            if arr > arrival_by or dep < horizon_start:
                continue
            if allowed_modes is not None and MODE_NAMES.get(connection["route_type"], "ov") not in allowed_modes:
                continue
            for boardings in range(1, max_boardings + 1):
                trip_key = (connection["trip_id"], boardings)
                onboard = trip_states.get(trip_key)
                if onboard is None:
                    future = labels[boardings - 1].get(connection["to_group"])
                    buffer = 0 if boardings == 1 else transfer_buffer
                    if not future or arr.timestamp() + buffer > future["departure"] or connection["drop_off_type"] == 1:
                        continue
                    decision = None
                    if bicycle_onboard:
                        decision = bicycle_onboard_decision(
                            connection["agency_id"], connection["route_type"], connection["short_name"],
                            connection["product_name"] or connection["long_name"], dep, arr,
                        )
                        if not decision.allowed:
                            continue
                    rides = [self._new_ride(connection, decision), *[dict(item) for item in future["rides"]]]
                    onboard = {
                        "departure": dep.timestamp(), "rides": rides,
                        "egress_group": future["egress_group"], "current_group": connection["from_group"],
                    }
                    trip_states[trip_key] = onboard
                else:
                    if onboard["current_group"] != connection["to_group"]:
                        continue
                    decision = None
                    if bicycle_onboard:
                        decision = bicycle_onboard_decision(
                            connection["agency_id"], connection["route_type"], connection["short_name"],
                            connection["product_name"] or connection["long_name"], dep, arr,
                        )
                        if not decision.allowed:
                            trip_states.pop(trip_key, None)
                            continue
                    rides = [dict(item) for item in onboard["rides"]]
                    rides[0] = dict(rides[0])
                    start_group = self._groups[connection["from_group"]]
                    rides[0]["from_group"] = connection["from_group"]
                    rides[0]["from_name"] = start_group.name
                    rides[0]["departure"] = dep.isoformat()
                    rides[0]["coordinates"] = [[start_group.lon, start_group.lat], *rides[0]["coordinates"]]
                    onboard = {**onboard, "departure": dep.timestamp(), "rides": rides, "current_group": connection["from_group"]}
                    trip_states[trip_key] = onboard
                if connection["pickup_type"] != 1:
                    old = labels[boardings].get(connection["from_group"])
                    if not old or onboard["departure"] > old["departure"]:
                        labels[boardings][connection["from_group"]] = onboard

        choices = []
        for boardings in range(1, max_boardings + 1):
            for group_id, access_info in access.items():
                state = labels[boardings].get(group_id)
                if not state:
                    continue
                origin_departure = state["departure"] - board_buffer - access_info["duration_s"]
                choices.append((origin_departure, boardings, group_id, state))
        if not choices:
            return None
        origin_departure, boardings, access_group, state = max(choices, key=lambda item: item[0])
        access_info = access[access_group]
        egress_info = egress[state["egress_group"]]
        rides = state["rides"]
        ticket_map = {}
        warnings = []
        for ride in rides:
            if ride.get("ticket_name"):
                ticket_map[ride["ticket_name"]] = float(ride.get("supplement_eur", 0))
            if ride.get("capacity_warning") and ride["capacity_warning"] not in warnings:
                warnings.append(ride["capacity_warning"])
        first_board = datetime.fromisoformat(rides[0]["departure"])
        last_arrival = datetime.fromisoformat(rides[-1]["arrival"])
        actual_arrival = last_arrival.timestamp() + exit_buffer + egress_info["duration_s"]
        wait_s = max(0, (first_board.timestamp() - origin_departure) - access_info["duration_s"] - board_buffer)
        return {
            "departure": datetime.fromtimestamp(origin_departure, AMSTERDAM_TZ).isoformat(),
            "arrival": datetime.fromtimestamp(actual_arrival, AMSTERDAM_TZ).isoformat(),
            "access": access_info, "egress": egress_info, "rides": rides,
            "transfers": boardings - 1, "wait_duration_min": round(wait_s / 60),
            "buffer_duration_min": round((board_buffer + (boardings - 1) * transfer_buffer + exit_buffer) / 60),
            "transit_duration_min": max(1, round((last_arrival - first_board).total_seconds() / 60)),
            "access_duration_min": max(1, round(access_info["duration_s"] / 60)),
            "egress_duration_min": max(1, round((egress_info["duration_s"] + exit_buffer) / 60)),
            "supplement_eur": round(sum(ticket_map.values()), 2),
            "tickets": [{"name": name, "price_eur": price} for name, price in ticket_map.items()],
            "warnings": warnings,
        }

    def _new_ride(self, connection: dict[str, Any], decision: Any) -> dict[str, Any]:
        start = self._groups[connection["from_group"]]
        end = self._groups[connection["to_group"]]
        return {
            "mode": MODE_NAMES.get(connection["route_type"], "ov"),
            "route_type": connection["route_type"],
            "agency_id": connection["agency_id"],
            "line": connection["short_name"] or connection["long_name"] or MODE_NAMES.get(connection["route_type"], "OV"),
            "direction": connection["headsign"],
            "trip_id": connection["trip_id"],
            "from_group": connection["from_group"], "from_name": start.name,
            "to_group": connection["to_group"], "to_name": end.name,
            "departure": connection["departure"].isoformat(), "arrival": connection["arrival"].isoformat(),
            "platforms": list(start.platforms),
            "color": f"#{connection['color']}" if connection["color"] else None,
            "coordinates": [[start.lon, start.lat], [end.lon, end.lat]],
            "bike_rule": decision.reason if decision else None,
            "ticket_name": decision.ticket_name if decision else None,
            "supplement_eur": decision.supplement_eur if decision else 0,
            "capacity_warning": decision.capacity_warning if decision else None,
            "rule_source": decision.source if decision else None,
            "rule_checked_on": decision.checked_on if decision else None,
        }


transit_data = TransitData()
