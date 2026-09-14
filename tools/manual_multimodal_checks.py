"""Reproduceerbare controles op de lokaal gecachete Amsterdamse GTFS-data."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.transit_data import transit_data
from app.transit_rules import AMSTERDAM_TZ, bicycle_onboard_decision


def next_weekday(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def route_case(name, origin, destination, mode, bicycle, moment, radius):
    route = transit_data.find_journey(
        origin, destination, moment,
        access_mode="bike" if bicycle else "walk", egress_mode="bike" if bicycle else "walk",
        bicycle_onboard=bicycle, max_access_m=radius, max_egress_m=radius,
        max_transfers=1, bicycle_speed_kmh=16, allowed_modes={mode},
    )
    if not route:
        raise AssertionError(f"Geen route voor controle {name}")
    return {
        "name": name,
        "rides": [{"mode": ride["mode"], "line": ride["line"], "from": ride["from_name"], "to": ride["to_name"]} for ride in route["rides"]],
        "supplement_eur": route["supplement_eur"],
    }


def main() -> int:
    status = transit_data.status()
    if status["state"] != "ready":
        raise SystemExit(status["message"])
    day = next_weekday(date.fromisoformat(status["window_start"]))
    moment = datetime(day.year, day.month, day.day, 11, tzinfo=AMSTERDAM_TZ)
    results = [
        route_case("tram 26 met fiets", (52.3791, 4.9003), (52.3550, 4.9970), "tram", True, moment, 3500),
        route_case("metro 52 met fiets", (52.4018, 4.9320), (52.3392, 4.8740), "metro", True, moment, 1500),
        route_case("NS naar Schiphol met fiets", (52.3791, 4.9003), (52.3090, 4.7630), "trein", True, moment, 2000),
        route_case("F3-pont met fiets", (52.3795, 4.9000), (52.3845, 4.9020), "pont", True, moment, 350),
        route_case("bus 22 zonder fiets", (52.3791, 4.9003), (52.3890, 4.8380), "bus", False, moment, 1200),
    ]
    peak_start = datetime(day.year, day.month, day.day, 6, 55, tzinfo=AMSTERDAM_TZ)
    peak_end = datetime(day.year, day.month, day.day, 7, 5, tzinfo=AMSTERDAM_TZ)
    if bicycle_onboard_decision("GVB", 1, "52", "", peak_start, peak_end).allowed:
        raise AssertionError("GVB-rit over de ochtendspitsgrens werd ten onrechte toegestaan")
    results.append({"name": "spitsgrens", "allowed": False, "checked_interval": "06:55–07:05"})
    print(json.dumps({"date": day.isoformat(), "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
