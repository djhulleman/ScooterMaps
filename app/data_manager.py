from __future__ import annotations

import gzip
import json
import os
import pickle
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import networkx as nx

from .demo_network import build_demo_graph
from .local_rules import local_metadata


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
GRAPH_PATH = DATA_DIR / "amsterdam_network.pkl.gz"
NDW_PATH = DATA_DIR / "ndw_signs.json"
CACHE_SCHEMA_VERSION = 3

# OSMnx keeps only a small default set of way attributes. These access tags
# distinguish an ordinary cycleway from a mandatory fiets/bromfietspad and
# from a carriageway that mofa/moped traffic may not use.
ROUTING_WAY_TAGS = (
    "bicycle", "cycleway", "cycleway:both", "cycleway:left", "cycleway:right",
    "mofa", "moped", "vehicle", "motor_vehicle",
    "oneway:mofa", "oneway:moped", "oneway:bicycle",
    "traffic_sign", "source:traffic_sign", "route", "duration",
)
ROUTING_NODE_TAGS = ("traffic_sign", "mofa", "moped", "bicycle", "barrier", "access")


class NetworkManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._graph: nx.MultiDiGraph = build_demo_graph()
        self._mode = "demo"
        self._status: dict[str, Any] = {
            "state": "ready",
            "progress": 5,
            "message": "Veilig demonstratienetwerk actief; Amsterdamse OSM-data is nog niet voorbereid.",
            "mode": "demo",
            "data_date": local_metadata()["checked_on"],
            "source_version": local_metadata()["version"],
        }
        self._preparing = False
        if os.environ.get("SCOOTERMAPS_DEMO_ONLY") != "1":
            self._load_cached_graph()

    def _load_cached_graph(self) -> None:
        if not GRAPH_PATH.exists():
            return
        try:
            with gzip.open(GRAPH_PATH, "rb") as handle:
                payload = pickle.load(handle)
            if not isinstance(payload, dict) or payload.get("routing_schema_version") != CACHE_SCHEMA_VERSION:
                raise ValueError("verouderde kaartcache zonder brom-/snorfietstoegangstags")
            graph = payload["graph"] if isinstance(payload, dict) else payload
            with self._lock:
                self._graph = graph
                self._mode = "osm"
                self._status.update({
                    "state": "ready",
                    "progress": 100,
                    "message": "Amsterdamse OSM-netwerkcache geladen.",
                    "mode": "osm",
                    "data_date": payload.get("created_at", "onbekend")[:10] if isinstance(payload, dict) else "onbekend",
                    "source_version": payload.get("source_version", "OpenStreetMap") if isinstance(payload, dict) else "OpenStreetMap",
                })
        except Exception as exc:  # Cache damage should never prevent local startup.
            self._status.update({"state": "warning", "message": f"Netwerkcache kon niet worden gelezen; demonstratienetwerk actief ({exc})."})

    def graph(self) -> nx.MultiDiGraph:
        with self._lock:
            return self._graph

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def start_prepare(self) -> bool:
        if os.environ.get("SCOOTERMAPS_SKIP_DOWNLOAD") == "1":
            return False
        with self._lock:
            if self._preparing or self._mode == "osm":
                return False
            self._preparing = True
            self._status.update({"state": "preparing", "progress": 10, "message": "Amsterdamse kaartdata voorbereiden…"})
        thread = threading.Thread(target=self._prepare, name="scootermaps-data", daemon=True)
        thread.start()
        return True

    def _set_progress(self, progress: int, message: str) -> None:
        with self._lock:
            self._status.update({"state": "preparing", "progress": progress, "message": message})

    def _prepare(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._set_progress(18, "Routeringsbibliotheek laden…")
            import osmnx as ox

            # Apply before graph_from_bbox: OSMnx discards non-default OSM tags
            # while it constructs graph edges and nodes.
            ox.settings.useful_tags_way = list(dict.fromkeys([
                *ox.settings.useful_tags_way,
                *ROUTING_WAY_TAGS,
            ]))
            ox.settings.useful_tags_node = list(dict.fromkeys([
                *ox.settings.useful_tags_node,
                *ROUTING_NODE_TAGS,
            ]))

            self._set_progress(28, "Wegennetwerk voor Amsterdam downloaden via OpenStreetMap…")
            custom_filter = '["highway"]["area"!="yes"]["highway"!~"footway|construction|proposed|raceway"]'
            west, south, east, north = 4.72, 52.29, 5.02, 52.43
            try:
                graph = ox.graph_from_bbox((west, south, east, north), simplify=True, retain_all=False, custom_filter=custom_filter)
            except TypeError:
                graph = ox.graph_from_bbox(north, south, east, west, simplify=True, retain_all=False, custom_filter=custom_filter)

            self._set_progress(72, "Wegkenmerken controleren en lokale cache schrijven…")
            for _, _, _, data in graph.edges(keys=True, data=True):
                data.setdefault("length", 1.0)

            payload = {
                "graph": graph,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_version": f"OpenStreetMap via OSMnx {getattr(ox, '__version__', 'onbekend')}",
                "routing_schema_version": CACHE_SCHEMA_VERSION,
            }
            temp_path = GRAPH_PATH.with_suffix(".tmp")
            with gzip.open(temp_path, "wb") as handle:
                pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
            temp_path.replace(GRAPH_PATH)

            self._set_progress(86, "NDW-verkeersborden als validatielaag ophalen…")
            ndw_result = self._fetch_ndw_validation()
            with self._lock:
                self._graph = graph
                self._mode = "osm"
                self._status.update({
                    "state": "ready",
                    "progress": 100,
                    "message": "Amsterdamse OSM-data is gereed en lokaal gecachet." + ndw_result,
                    "mode": "osm",
                    "data_date": payload["created_at"][:10],
                    "source_version": payload["source_version"],
                })
        except Exception as exc:
            with self._lock:
                self._status.update({
                    "state": "warning",
                    "progress": 100,
                    "message": f"Download niet gelukt; veilig demonstratienetwerk blijft actief. {type(exc).__name__}: {exc}",
                    "mode": "demo",
                })
        finally:
            with self._lock:
                self._preparing = False

    @staticmethod
    def _fetch_ndw_validation() -> str:
        endpoint = "https://data.ndw.nu/api/rest/static-road-data/traffic-signs/v1/current-state"
        records: list[dict[str, Any]] = []
        try:
            with httpx.Client(timeout=25.0, headers={"User-Agent": "ScooterMaps-local-poc/0.1"}) as client:
                for code in ("G11", "G12", "G13"):
                    response = client.get(endpoint, params={"town-code": "GM0363", "rvv-code": code})
                    response.raise_for_status()
                    values = response.json()
                    if isinstance(values, list):
                        records.extend(values)
            NDW_PATH.write_text(json.dumps({"downloaded_at": datetime.now(timezone.utc).isoformat(), "records": records}, ensure_ascii=False), encoding="utf-8")
            return f" NDW-validatielaag: {len(records)} borden."
        except Exception:
            return " NDW-validatielaag was tijdelijk niet beschikbaar."


network_manager = NetworkManager()
