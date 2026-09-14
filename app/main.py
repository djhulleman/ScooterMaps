from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .data_manager import network_manager
from .geocoding import geocode
from .local_rules import load_rules, local_metadata
from .overrides import add_override, delete_override, load_collection, update_override
from .routing import VehicleSettings, calculate_routes
from .bicycle_routing import BicycleSettings, calculate_bicycle_routes
from .multimodal import JourneySettings, plan_journeys
from .transit_data import transit_data
from .transit_rules import AMSTERDAM_TZ, load_transit_rules


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "app" / "static"


class Coordinate(BaseModel):
    lat: float = Field(ge=52.20, le=52.50)
    lon: float = Field(ge=4.60, le=5.20)


class RouteRequest(BaseModel):
    start: Coordinate
    end: Coordinate
    vehicle: Literal["snorfiets", "bromfiets"]
    preference: Literal["fast", "quiet"] = "fast"
    powertrain: Literal["electric", "fuel"] = "electric"
    build_year: int = Field(default=2020, ge=1950, le=2035)
    exemption: bool = False


class ModeToggles(BaseModel):
    train: bool = True
    metro: bool = True
    tram: bool = True
    bus: bool = True
    ferry: bool = True


class CostWeights(BaseModel):
    duration_min: float = Field(default=1.0, ge=0, le=20)
    bike_km: float = Field(default=0.35, ge=0, le=20)
    walk_min: float = Field(default=0.8, ge=0, le=20)
    transfer: float = Field(default=7.0, ge=0, le=60)
    supplement_eur: float = Field(default=2.0, ge=0, le=30)


class BicycleRouteRequest(BaseModel):
    start: Coordinate
    end: Coordinate
    bicycle_type: Literal["regular", "ebike"] = "regular"
    preference: Literal["fast", "comfortable", "quiet"] = "fast"


class JourneyRequest(BicycleRouteRequest):
    mode: Literal["bike_all", "bike_transit", "park_transit", "ov_fiets", "compare", "ov_only", "return_to_bike"] = "bike_all"
    departure_time: datetime | None = None
    arrive_by: bool = False
    average_speed_kmh: float | None = Field(default=None, ge=6, le=35)
    max_access_bike_km: float = Field(default=6.0, ge=0.2, le=25)
    max_egress_bike_km: float = Field(default=6.0, ge=0.2, le=25)
    max_transfers: int = Field(default=1, ge=0, le=3)
    secure_parking: bool = True
    boarding_buffer_min: float | None = Field(default=None, ge=0, le=30)
    transfer_buffer_min: float | None = Field(default=None, ge=0, le=30)
    exit_buffer_min: float | None = Field(default=None, ge=0, le=30)
    allowed_modes: ModeToggles = Field(default_factory=ModeToggles)
    cost_weights: CostWeights = Field(default_factory=CostWeights)


@asynccontextmanager
async def lifespan(_: FastAPI):
    network_manager.start_prepare()
    yield


app = FastAPI(
    title="ScooterMaps",
    description="Lokale multimodale routeplanner voor scooter, fiets en openbaar vervoer in Amsterdam.",
    version="0.2.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "status": network_manager.status()}


@app.get("/api/status")
def status() -> dict[str, Any]:
    network = network_manager.status()
    return {**network, "network": network, "transit": transit_data.status()}


@app.post("/api/prepare")
def prepare() -> dict[str, Any]:
    started = network_manager.start_prepare()
    return {"started": started, "status": network_manager.status()}


@app.get("/api/metadata")
def metadata() -> dict[str, Any]:
    rules = load_rules()
    return {
        "local_rules": local_metadata(),
        "network": network_manager.status(),
        "transit": transit_data.status(),
        "transit_rules": load_transit_rules()["metadata"],
        "exception_streets": len(rules["snorfiets_exception_streets"]),
        "prohibited_areas": len(rules["prohibited_areas"]),
        "legal_notice": "Proof of concept. Verkeersborden en de actuele situatie ter plaatse blijven leidend.",
    }


@app.get("/api/geocode")
async def geocode_address(q: str = Query(min_length=3, max_length=180)) -> dict[str, Any]:
    try:
        results, cached = await geocode(q)
        return {"results": results, "cached": cached}
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Adres zoeken is tijdelijk niet beschikbaar. Kies begin en einde op de kaart.",
        ) from exc


@app.post("/api/route")
def route(request: RouteRequest) -> dict[str, Any]:
    try:
        return calculate_routes(
            (request.start.lat, request.start.lon),
            (request.end.lat, request.end.lon),
            VehicleSettings(
                profile=request.vehicle,
                powertrain=request.powertrain,
                build_year=request.build_year,
                exemption=request.exemption,
            ),
            request.preference,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/bicycle/route")
def bicycle_route(request: BicycleRouteRequest) -> dict[str, Any]:
    try:
        return calculate_bicycle_routes(
            (request.start.lat, request.start.lon),
            (request.end.lat, request.end.lon),
            BicycleSettings(request.bicycle_type),
            request.preference,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/transit/status")
def transit_status() -> dict[str, Any]:
    return transit_data.status()


@app.post("/api/journey")
def journey(request: JourneyRequest) -> dict[str, Any]:
    allowed_modes = frozenset(name for name, enabled in request.allowed_modes.model_dump().items() if enabled)
    if request.mode != "bike_all" and not allowed_modes:
        raise HTTPException(status_code=422, detail="Zet minimaal één OV-vervoersvorm aan.")
    when = request.departure_time or datetime.now(AMSTERDAM_TZ)
    try:
        return plan_journeys(
            (request.start.lat, request.start.lon),
            (request.end.lat, request.end.lon),
            when,
            JourneySettings(
                mode=request.mode, bicycle_type=request.bicycle_type, preference=request.preference,
                average_speed_kmh=request.average_speed_kmh,
                max_access_bike_km=request.max_access_bike_km,
                max_egress_bike_km=request.max_egress_bike_km,
                max_transfers=request.max_transfers, secure_parking=request.secure_parking,
                boarding_buffer_min=request.boarding_buffer_min,
                transfer_buffer_min=request.transfer_buffer_min,
                exit_buffer_min=request.exit_buffer_min,
                allowed_modes=allowed_modes, cost_weights=request.cost_weights.model_dump(),
            ),
            arrive_by=request.arrive_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/overrides")
def list_overrides() -> dict[str, Any]:
    return load_collection()


@app.post("/api/overrides", status_code=201)
def create_override(feature: dict[str, Any] = Body(...)) -> dict[str, Any]:
    properties = feature.get("properties", {})
    allowed_corrections = {
        "forbidden_snorfiets", "forbidden_bromfiets", "allowed",
        "wrong_direction", "wrong_path_type", "temporary", "other",
    }
    if properties.get("correction") not in allowed_corrections:
        raise HTTPException(status_code=422, detail="Onbekend correctietype.")
    if properties.get("vehicle") not in {"snorfiets", "bromfiets", "both"}:
        raise HTTPException(status_code=422, detail="Onbekend voertuigprofiel.")
    return add_override(feature)


@app.patch("/api/overrides/{item_id}")
def patch_override(item_id: str, properties: dict[str, Any] = Body(...)) -> dict[str, Any]:
    updated = update_override(item_id, properties)
    if not updated:
        raise HTTPException(status_code=404, detail="Correctie niet gevonden.")
    return updated


@app.delete("/api/overrides/{item_id}", status_code=204)
def remove_override(item_id: str) -> None:
    if not delete_override(item_id):
        raise HTTPException(status_code=404, detail="Correctie niet gevonden.")
