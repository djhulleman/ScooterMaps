from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import sqlite3
import sys
import tempfile
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = "https://gtfs.ovapi.nl/nl/gtfs-nl.zip"
DEFAULT_ARCHIVE = ROOT / "data" / "gtfs" / "gtfs-nl.zip"
DEFAULT_OUTPUT = ROOT / "data" / "gtfs" / "amsterdam-transit.sqlite"
AGENCIES = {"GVB", "IFF:NS"}
BBOX = (4.60, 52.24, 5.20, 52.52)


def _rows(archive: zipfile.ZipFile, name: str):
    with archive.open(name) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream)


def _download(source: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, dir=target.parent, suffix=".download") as temp:
        temp_path = Path(temp.name)
    try:
        request = urllib.request.Request(source, headers={"User-Agent": "ScooterMaps-local-poc/0.2"})
        with urllib.request.urlopen(request, timeout=120) as response, temp_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        temp_path.replace(target)
    finally:
        temp_path.unlink(missing_ok=True)


def _schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE agencies (agency_id TEXT PRIMARY KEY, agency_name TEXT NOT NULL);
        CREATE TABLE routes (
            route_id TEXT PRIMARY KEY, agency_id TEXT NOT NULL, short_name TEXT,
            long_name TEXT, route_type INTEGER NOT NULL, color TEXT, text_color TEXT
        );
        CREATE TABLE trips (
            trip_id TEXT PRIMARY KEY, route_id TEXT NOT NULL, service_id TEXT NOT NULL,
            headsign TEXT, product_name TEXT, direction_id TEXT, bikes_allowed TEXT
        );
        CREATE TABLE stops (
            stop_id TEXT PRIMARY KEY, name TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
            location_type INTEGER, parent_station TEXT, platform_code TEXT
        );
        CREATE TABLE service_dates (service_id TEXT NOT NULL, date TEXT NOT NULL, exception_type INTEGER NOT NULL);
        CREATE TABLE stop_times (
            trip_id TEXT NOT NULL, stop_sequence INTEGER NOT NULL, stop_id TEXT NOT NULL,
            arrival_secs INTEGER NOT NULL, departure_secs INTEGER NOT NULL,
            pickup_type INTEGER, drop_off_type INTEGER
        );
    """)


def _gtfs_seconds(value: str) -> int:
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def build_subset(archive_path: Path, output_path: Path, *, days: int = 56) -> dict[str, int | str]:
    if not archive_path.exists():
        raise FileNotFoundError(f"GTFS-archief niet gevonden: {archive_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".building.sqlite")
    temp_path.unlink(missing_ok=True)
    today = date.today()
    window_start = today - timedelta(days=1)
    window_end = today + timedelta(days=days)

    with zipfile.ZipFile(archive_path) as archive, sqlite3.connect(temp_path) as db:
        required = {"agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt", "calendar_dates.txt"}
        missing = required - set(archive.namelist())
        if missing:
            raise ValueError(f"Ongeldige GTFS-feed; ontbrekend: {', '.join(sorted(missing))}")
        _schema(db)

        agencies = {row["agency_id"]: row for row in _rows(archive, "agency.txt") if row["agency_id"] in AGENCIES}
        db.executemany("INSERT INTO agencies VALUES (?, ?)", [(key, row["agency_name"]) for key, row in agencies.items()])

        service_rows = []
        active_services: set[str] = set()
        available_dates: list[date] = []
        for row in _rows(archive, "calendar_dates.txt"):
            parsed = datetime.strptime(row["date"], "%Y%m%d").date()
            if window_start <= parsed <= window_end:
                service_rows.append((row["service_id"], parsed.isoformat(), int(row["exception_type"])))
                if row["exception_type"] == "1":
                    active_services.add(row["service_id"])
                    available_dates.append(parsed)
        if not service_rows:
            raise ValueError("GTFS-feed heeft geen geldige kalenderdagen in het gekozen venster.")
        db.executemany("INSERT INTO service_dates VALUES (?, ?, ?)", service_rows)

        route_rows = {}
        for row in _rows(archive, "routes.txt"):
            if row["agency_id"] in AGENCIES:
                route_rows[row["route_id"]] = (
                    row["route_id"], row["agency_id"], row["route_short_name"], row["route_long_name"],
                    int(row["route_type"]), row.get("route_color", ""), row.get("route_text_color", ""),
                )
        db.executemany("INSERT INTO routes VALUES (?, ?, ?, ?, ?, ?, ?)", route_rows.values())

        trip_rows = {}
        for row in _rows(archive, "trips.txt"):
            if row["route_id"] in route_rows and row["service_id"] in active_services:
                trip_rows[row["trip_id"]] = (
                    row["trip_id"], row["route_id"], row["service_id"], row.get("trip_headsign", ""),
                    row.get("trip_long_name", "") or row.get("trip_short_name", ""),
                    row.get("direction_id", ""), row.get("bikes_allowed", ""),
                )
        if not trip_rows:
            raise ValueError("GTFS-feed bevat geen GVB- of NS-ritten in het gekozen datumvenster.")
        db.executemany("INSERT INTO trips VALUES (?, ?, ?, ?, ?, ?, ?)", trip_rows.values())

        west, south, east, north = BBOX
        stop_rows = {}
        for row in _rows(archive, "stops.txt"):
            try:
                lat, lon = float(row["stop_lat"]), float(row["stop_lon"])
            except (TypeError, ValueError):
                continue
            if west <= lon <= east and south <= lat <= north:
                stop_rows[row["stop_id"]] = (
                    row["stop_id"], row["stop_name"], lat, lon, int(row.get("location_type") or 0),
                    row.get("parent_station", ""), row.get("platform_code", ""),
                )
        db.executemany("INSERT INTO stops VALUES (?, ?, ?, ?, ?, ?, ?)", stop_rows.values())

        kept_trip_ids = set(trip_rows)
        kept_stop_ids = set(stop_rows)
        batch = []
        kept_counts: dict[str, int] = {}
        for row in _rows(archive, "stop_times.txt"):
            trip_id = row["trip_id"]
            if trip_id not in kept_trip_ids or row["stop_id"] not in kept_stop_ids:
                continue
            try:
                item = (
                    trip_id, int(row["stop_sequence"]), row["stop_id"],
                    _gtfs_seconds(row["arrival_time"]), _gtfs_seconds(row["departure_time"]),
                    int(row.get("pickup_type") or 0), int(row.get("drop_off_type") or 0),
                )
            except (ValueError, KeyError):
                continue
            batch.append(item)
            kept_counts[trip_id] = kept_counts.get(trip_id, 0) + 1
            if len(batch) >= 25_000:
                db.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
                batch.clear()
        if batch:
            db.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?, ?, ?)", batch)

        invalid = [trip_id for trip_id, count in kept_counts.items() if count < 2]
        missing_trips = kept_trip_ids - set(kept_counts)
        for chunk_start in range(0, len(invalid) + len(missing_trips), 800):
            chunk = (invalid + list(missing_trips))[chunk_start:chunk_start + 800]
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            db.execute(f"DELETE FROM stop_times WHERE trip_id IN ({placeholders})", chunk)
            db.execute(f"DELETE FROM trips WHERE trip_id IN ({placeholders})", chunk)

        db.executescript("""
            CREATE INDEX idx_service_dates_date ON service_dates(date, exception_type, service_id);
            CREATE INDEX idx_trips_service ON trips(service_id, trip_id);
            CREATE INDEX idx_stop_times_trip ON stop_times(trip_id, stop_sequence);
            CREATE INDEX idx_stop_times_stop ON stop_times(stop_id);
        """)
        final_trips = db.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
        final_stop_times = db.execute("SELECT COUNT(*) FROM stop_times").fetchone()[0]
        metadata = {
            "source_url": DEFAULT_SOURCE,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "window_start": min(available_dates).isoformat(),
            "window_end": max(available_dates).isoformat(),
            "agencies": len(agencies),
            "routes": len(route_rows),
            "trips": final_trips,
            "stops": len(stop_rows),
            "stop_times": final_stop_times,
            "geometry": "rechte lijnen tussen haltes; GTFS-shapes niet opgenomen in regionale cache",
        }
        db.executemany("INSERT INTO meta VALUES (?, ?)", [(key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()])
        db.commit()

    db.close()
    temp_path.replace(output_path)
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="Download GTFS-NL en bouw een compacte Amsterdam-cache voor ScooterMaps.")
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--days", type=int, default=56)
    parser.add_argument("--offline", action="store_true", help="Gebruik het reeds gedownloade zipbestand.")
    args = parser.parse_args()
    try:
        if not args.offline:
            print(f"GTFS downloaden: {args.source}")
            _download(args.source, args.archive)
        print("Regionale GVB/NS-cache bouwen; dit kan enkele minuten duren…")
        metadata = build_subset(args.archive, args.output, days=max(7, args.days))
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        print(f"Gereed: {args.output}")
        return 0
    except Exception as exc:
        print(f"FOUT: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
