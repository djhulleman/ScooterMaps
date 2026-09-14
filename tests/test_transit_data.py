import json
import sqlite3
from datetime import datetime

import pytest

from app.transit_data import TransitData, gtfs_time_to_seconds
from app.transit_rules import AMSTERDAM_TZ


def make_cache(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE routes (route_id TEXT PRIMARY KEY, agency_id TEXT, short_name TEXT, long_name TEXT, route_type INTEGER, color TEXT, text_color TEXT);
            CREATE TABLE trips (trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT, headsign TEXT, product_name TEXT, direction_id TEXT, bikes_allowed TEXT);
            CREATE TABLE stops (stop_id TEXT PRIMARY KEY, name TEXT, lat REAL, lon REAL, location_type INTEGER, parent_station TEXT, platform_code TEXT);
            CREATE TABLE service_dates (service_id TEXT, date TEXT, exception_type INTEGER);
            CREATE TABLE stop_times (trip_id TEXT, stop_sequence INTEGER, stop_id TEXT, arrival_secs INTEGER, departure_secs INTEGER, pickup_type INTEGER, drop_off_type INTEGER);
        """)
        db.executemany("INSERT INTO meta VALUES (?, ?)", [("window_start", json.dumps("2026-09-14")), ("window_end", json.dumps("2026-09-16"))])
        db.execute("INSERT INTO routes VALUES ('m52','GVB','52','Noord-Zuidlijn',1,'183f6d','ffffff')")
        db.executemany("INSERT INTO trips VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("day", "m52", "weekday", "Zuid", "", "0", "1"),
            ("night", "m52", "weekday", "Zuid", "", "0", "1"),
        ])
        db.executemany("INSERT INTO stops VALUES (?, ?, ?, ?, 0, ?, ?)", [
            ("a", "Station A", 52.3700, 4.9000, "A", "1"),
            ("b", "Station B", 52.3500, 4.9100, "B", "2"),
        ])
        db.execute("INSERT INTO service_dates VALUES ('weekday','2026-09-14',1)")
        db.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?, 0, 0)", [
            ("day", 1, "a", 36000, 36000), ("day", 2, "b", 37200, 37200),
            ("night", 1, "a", 90000, 90000), ("night", 2, "b", 90900, 90900),
        ])
    return TransitData(path)


def test_gtfs_time_supports_after_midnight_and_rejects_invalid_values():
    assert gtfs_time_to_seconds("25:05:30") == 90330
    with pytest.raises(ValueError):
        gtfs_time_to_seconds("24:70:00")


def test_calendar_and_direct_journey(tmp_path):
    data = make_cache(tmp_path / "mini.sqlite")
    journey = data.find_journey(
        (52.3700, 4.9000), (52.3500, 4.9100), datetime(2026, 9, 14, 9, 55, tzinfo=AMSTERDAM_TZ),
        access_mode="walk", egress_mode="walk", bicycle_onboard=True,
        max_access_m=200, max_egress_m=200, max_transfers=1, bicycle_speed_kmh=16,
        boarding_buffer_min=0, transfer_buffer_min=0, exit_buffer_min=0,
    )
    assert journey is not None
    assert journey["rides"][0]["line"] == "52"
    assert journey["supplement_eur"] == 2.30


def test_boarding_margin_is_not_reported_as_schedule_wait(tmp_path):
    data = make_cache(tmp_path / "wait-versus-margin.sqlite")
    journey = data.find_journey(
        (52.3700, 4.9000), (52.3500, 4.9100), datetime(2026, 9, 14, 9, 55, tzinfo=AMSTERDAM_TZ),
        access_mode="walk", egress_mode="walk", bicycle_onboard=True,
        max_access_m=200, max_egress_m=200, max_transfers=0, bicycle_speed_kmh=16,
        boarding_buffer_min=4, transfer_buffer_min=0, exit_buffer_min=3,
    )
    assert journey is not None
    assert journey["wait_duration_min"] == 1
    assert journey["buffer_duration_min"] == 7


def test_impractically_long_wait_is_not_returned(tmp_path):
    data = make_cache(tmp_path / "long-wait.sqlite")
    journey = data.find_journey(
        (52.3700, 4.9000), (52.3500, 4.9100), datetime(2026, 9, 14, 8, 0, tzinfo=AMSTERDAM_TZ),
        access_mode="walk", egress_mode="walk", bicycle_onboard=False,
        max_access_m=200, max_egress_m=200, max_transfers=0, bicycle_speed_kmh=16,
        boarding_buffer_min=0, transfer_buffer_min=0, exit_buffer_min=0,
    )
    assert journey is None


def test_previous_service_day_supplies_trip_after_midnight(tmp_path):
    data = make_cache(tmp_path / "night.sqlite")
    journey = data.find_journey(
        (52.3700, 4.9000), (52.3500, 4.9100), datetime(2026, 9, 15, 0, 55, tzinfo=AMSTERDAM_TZ),
        access_mode="walk", egress_mode="walk", bicycle_onboard=False,
        max_access_m=200, max_egress_m=200, max_transfers=0, bicycle_speed_kmh=16,
        boarding_buffer_min=0, transfer_buffer_min=0, exit_buffer_min=0,
    )
    assert journey is not None
    assert datetime.fromisoformat(journey["rides"][0]["departure"]).day == 15


def test_arrive_by_reverse_scan_chooses_a_trip_before_deadline(tmp_path):
    data = make_cache(tmp_path / "arrive.sqlite")
    journey = data.find_journey_arrive_by(
        (52.3700, 4.9000), (52.3500, 4.9100), datetime(2026, 9, 14, 10, 25, tzinfo=AMSTERDAM_TZ),
        access_mode="walk", egress_mode="walk", bicycle_onboard=False,
        max_access_m=200, max_egress_m=200, max_transfers=0, bicycle_speed_kmh=16,
        boarding_buffer_min=0, transfer_buffer_min=0, exit_buffer_min=0,
    )
    assert journey is not None
    assert datetime.fromisoformat(journey["arrival"]) <= datetime(2026, 9, 14, 10, 25, tzinfo=AMSTERDAM_TZ)
    assert journey["rides"][0]["departure"].startswith("2026-09-14T10:00")


def test_no_calendar_service_returns_no_journey(tmp_path):
    data = make_cache(tmp_path / "empty-day.sqlite")
    assert data.find_journey(
        (52.3700, 4.9000), (52.3500, 4.9100), datetime(2026, 9, 16, 10, tzinfo=AMSTERDAM_TZ),
        access_mode="walk", egress_mode="walk", bicycle_onboard=False,
        max_access_m=200, max_egress_m=200, max_transfers=0, bicycle_speed_kmh=16,
    ) is None


def test_query_outside_cache_window_gives_actionable_error(tmp_path):
    data = make_cache(tmp_path / "outside.sqlite")
    with pytest.raises(ValueError, match="vernieuw de dienstregeling"):
        data.find_journey(
            (52.3700, 4.9000), (52.3500, 4.9100), datetime(2027, 1, 1, 10, tzinfo=AMSTERDAM_TZ),
            access_mode="walk", egress_mode="walk", bicycle_onboard=False,
            max_access_m=200, max_egress_m=200, max_transfers=0, bicycle_speed_kmh=16,
        )


def test_amsterdam_timezone_observes_dst():
    winter = datetime(2026, 1, 15, 12, tzinfo=AMSTERDAM_TZ)
    summer = datetime(2026, 7, 15, 12, tzinfo=AMSTERDAM_TZ)
    assert winter.utcoffset().total_seconds() == 3600
    assert summer.utcoffset().total_seconds() == 7200
