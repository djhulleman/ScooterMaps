from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


DENY = {"no", "private"}
ALLOW = {"yes", "designated", "permissive", "official"}
HARD_ROAD_DENY = {"motorway", "motorway_link", "trunk", "trunk_link", "raceway"}


@dataclass(frozen=True)
class BicycleDecision:
    allowed: bool
    place: str
    reason: str
    confidence: str
    speed_kmh: float
    comfort_factor: float
    dismount: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _values(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip().lower() for item in value}
    return {part.strip().lower() for part in str(value).replace(";", "|").split("|")}


def evaluate_bicycle_access(tags: dict[str, Any], bicycle_type: str = "regular") -> BicycleDecision:
    """Conservative OSM bicycle interpretation, separate from scooter rules."""
    highway = next(iter(_values(tags.get("highway"))), "")
    bicycle = _values(tags.get("bicycle"))
    access = _values(tags.get("access"))
    route = _values(tags.get("route"))
    motorroad = _values(tags.get("motorroad"))
    base_speed = 22.0 if bicycle_type == "ebike" else 16.0

    if bicycle & DENY:
        return BicycleDecision(False, "verboden", "OSM markeert fietsverkeer als verboden.", "hoog", 0, 9)
    if access & DENY and not bicycle & ALLOW:
        return BicycleDecision(False, "verboden", "Algemene toegang is beperkt en er is geen fietstoestemming.", "middel", 0, 9)
    if highway in HARD_ROAD_DENY or "yes" in motorroad:
        return BicycleDecision(False, "verboden", "Auto(snel)weg of autoweg: fietsers zijn niet toegestaan.", "hoog", 0, 9)

    if "ferry" in route or highway == "ferry":
        return BicycleDecision(True, "pont", "Fiets toegestaan op deze veerverbinding.", "hoog" if bicycle else "middel", 12, 0.9)
    if "dismount" in bicycle or highway == "steps":
        return BicycleDecision(True, "lopen met fiets", "Hier afstappen en met de fiets aan de hand lopen.", "hoog", 4.5, 3.8, True)
    if highway == "cycleway":
        return BicycleDecision(True, "fietspad", "Vrijliggend of aangewezen fietspad.", "hoog", base_speed, 0.72)
    if highway in {"path", "track"}:
        if bicycle & ALLOW:
            return BicycleDecision(True, "fietspad", "Pad met expliciete fietstoegang.", "hoog", base_speed * 0.85, 0.82)
        return BicycleDecision(True, "onverhard/onzeker pad", "Pad zonder volledige fietstagging; controleer de borden.", "laag", base_speed * 0.7, 1.65)
    if highway in {"footway", "pedestrian"}:
        if bicycle & ALLOW:
            return BicycleDecision(True, "gedeeld pad", "Voetgangersgebied met expliciete fietstoegang.", "middel", min(base_speed, 10), 1.5)
        return BicycleDecision(False, "voetpad", "Geen expliciete fietstoegang op voetpad of voetgangersgebied.", "middel", 0, 9)

    cycle_tags = set()
    for key in ("cycleway", "cycleway:left", "cycleway:right", "cycleway:both"):
        cycle_tags |= _values(tags.get(key))
    if cycle_tags - {"no", "none", "separate"}:
        return BicycleDecision(True, "fietsstrook", "Rijbaan met fietsstrook of fietssuggestiestrook.", "middel", base_speed, 0.92)
    if highway in {"living_street", "residential", "service", "unclassified"}:
        return BicycleDecision(True, "rustige rijbaan", "Fiets toegestaan op een lokale rijbaan.", "middel", base_speed, 1.0)
    if highway in {"tertiary", "tertiary_link"}:
        return BicycleDecision(True, "rijbaan", "Fiets toegestaan; dit kan een drukkere weg zijn.", "middel", base_speed, 1.35)
    if highway in {"primary", "primary_link", "secondary", "secondary_link"}:
        return BicycleDecision(True, "drukke rijbaan", "Fiets niet expliciet verboden, maar dit is een drukke rijbaan.", "laag", base_speed, 2.1)
    if highway:
        return BicycleDecision(True, "onzeker wegvak", "Fietstoegang volgt uit OSM maar het wegtype is onvolledig.", "laag", base_speed * 0.85, 1.8)
    return BicycleDecision(False, "onbekend", "Wegtype ontbreekt; veilig uitgesloten.", "laag", 0, 9)
