from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any, Iterable


VEHICLE_KEYS = {"snorfiets": "mofa", "bromfiets": "moped"}
DENY_VALUES = {"no", "private", "agricultural", "forestry", "delivery", "use_sidepath"}
ALLOW_VALUES = {"yes", "designated", "permissive", "official"}
ROAD_HIGHWAYS = {
    "primary", "secondary", "tertiary", "unclassified", "residential",
    "living_street", "service", "road", "primary_link", "secondary_link",
    "tertiary_link",
}


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool | None
    profile: str
    place: str
    legal_speed_kmh: float
    routing_speed_kmh: float
    reason: str
    source: str
    confidence: str
    traffic_sign: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _scalar(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return ";".join(str(item) for item in value)
    return "" if value is None else str(value)


@lru_cache(maxsize=512)
def _split_values(value: str) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in value.replace(",", ";").split(";") if part.strip())


def _values(value: Any) -> frozenset[str]:
    return _split_values(_scalar(value))


def _has_any(tags: dict[str, Any], keys: Iterable[str], values: set[str]) -> bool:
    return any(_values(tags.get(key)) & values for key in keys)


def _parse_speed(value: Any, fallback: float) -> float:
    raw = _scalar(value).lower().replace("km/h", "").strip()
    if raw in {"walk", "signals", "none", "nl:urban", "nl:rural"}:
        return fallback
    try:
        return float(raw.split(";")[0])
    except (TypeError, ValueError):
        return fallback


def _match_override(overrides: list[dict[str, Any]] | None, tags: dict[str, Any], profile: str) -> dict[str, Any] | None:
    if not overrides:
        return None
    osm_id = str(tags.get("osmid", tags.get("id", "")))
    for item in overrides:
        properties = item.get("properties", item)
        if not properties.get("permanent", False):
            continue
        if properties.get("osm_id") and str(properties["osm_id"]) != osm_id:
            continue
        target = properties.get("vehicle", "both")
        if target not in {"both", profile}:
            continue
        return properties
    return None


def evaluate_edge_access(
    tags: dict[str, Any],
    profile: str,
    *,
    inside_snorfiets_zone: bool = False,
    exception_street: bool = False,
    prohibited_area: str | None = None,
    reverse_travel: bool = False,
    overrides: list[dict[str, Any]] | None = None,
) -> AccessDecision:
    """Central, deterministic access decision for one directed OSM edge.

    ``None`` means that the edge is not known to be prohibited, but the data is
    too weak or conflicting to claim legality. Routers may include it only while
    making the uncertainty prominent to the user.
    """
    if profile not in VEHICLE_KEYS:
        raise ValueError(f"Onbekend voertuigprofiel: {profile}")

    key = VEHICLE_KEYS[profile]
    highway = _scalar(tags.get("highway", "")).lower()
    sign = _scalar(tags.get("traffic_sign")) or None
    name = _scalar(tags.get("name"))
    source = "OSM-tags + RVV 1990"

    override = _match_override(overrides, tags, profile)
    if override:
        correction = override.get("correction", "")
        if correction in {"forbidden_snorfiets", "forbidden_bromfiets", "wrong_direction"}:
            return AccessDecision(False, profile, "onbekend", 0, 0, "Permanente lokale correctie: verboden.", "lokale override", "hoog", sign)
        if correction == "allowed":
            speed = 25.0 if profile == "snorfiets" else 45.0
            return AccessDecision(True, profile, override.get("place", "rijbaan"), speed, speed, "Permanente lokale correctie: toegestaan.", "lokale override", "hoog", sign)

    if prohibited_area:
        return AccessDecision(False, profile, "verboden gebied", 0, 0, f"Lokaal inrijverbod: {prohibited_area}.", "Gemeente Amsterdam", "hoog", sign)

    if highway in {"motorway", "motorway_link"} or _values(tags.get("motorroad")) & {"yes"}:
        return AccessDecision(False, profile, "rijbaan", 0, 0, "Brom- en snorfietsen zijn niet toegestaan op autosnelwegen en autowegen.", source, "hoog", sign)

    if highway == "steps":
        return AccessDecision(False, profile, "trap", 0, 0, "Een trap is niet berijdbaar voor snor- of bromfietsen.", "OSM wegtype", "hoog", sign)
    if highway in {"footway", "pedestrian"} and not (_values(tags.get(key)) & ALLOW_VALUES):
        return AccessDecision(False, profile, "voetgangersgebied", 0, 0, "Geen expliciete toegang voor dit voertuig op voetpad of voetgangersgebied.", "OSM wegtype", "middel", sign)

    # Explicit vehicle-specific tags win over generic inference.
    if _has_any(tags, [key], DENY_VALUES):
        values = _values(tags.get(key))
        if "use_sidepath" in values:
            place = "rijbaan" if highway in ROAD_HIGHWAYS else "onbekend"
            return AccessDecision(
                False,
                profile,
                place,
                0,
                0,
                f"OSM-tag {key}=use_sidepath: het aangewezen zijpad is verplicht.",
                "expliciete OSM-toegangstag",
                "hoog",
                sign,
            )
        return AccessDecision(False, profile, "onbekend", 0, 0, f"OSM-tag {key}=no/verboden.", "expliciete OSM-toegangstag", "hoog", sign)
    if _has_any(tags, ["access", "vehicle", "motor_vehicle"], DENY_VALUES) and not _has_any(tags, [key], ALLOW_VALUES):
        return AccessDecision(False, profile, "onbekend", 0, 0, "Algemene toegangstag sluit gemotoriseerd verkeer uit.", "expliciete OSM-toegangstag", "hoog", sign)

    oneway_value = _scalar(tags.get(f"oneway:{key}", tags.get("oneway", ""))).lower()
    if reverse_travel and oneway_value in {"yes", "1", "true"}:
        return AccessDecision(False, profile, "onbekend", 0, 0, "Verboden rijrichting door eenrichtingsverkeer.", "expliciete OSM-richtingstag", "hoog", sign)
    if not reverse_travel and oneway_value == "-1":
        return AccessDecision(False, profile, "onbekend", 0, 0, "Verboden rijrichting door voertuigspecifieke eenrichtingstag.", "expliciete OSM-richtingstag", "hoog", sign)

    is_ferry = _scalar(tags.get("route")).lower() == "ferry" or highway == "ferry"
    if is_ferry:
        if _has_any(tags, [key], DENY_VALUES) or "f9" in name.lower():
            return AccessDecision(False, profile, "pont", 0, 0, "Deze pont laat dit voertuig niet toe.", "GVB/OSM", "hoog", sign)
        if _has_any(tags, [key, "motor_vehicle"], ALLOW_VALUES):
            return AccessDecision(True, profile, "pont", 6, 6, "Voertuig is expliciet toegestaan; zet de motor op de pont uit.", "GVB + OSM", "hoog", sign)
        return AccessDecision(None, profile, "pont", 6, 6, "Toegang op deze pont is niet expliciet vastgelegd; controleer ter plaatse en zet de motor uit.", "GVB + OSM", "laag", sign)

    is_cycleway = highway in {"cycleway", "path"}
    g12a = bool(sign and "G12" in sign.upper()) or _has_any(tags, ["moped"], {"designated"})
    g11 = bool(sign and "G11" in sign.upper()) or (highway == "cycleway" and not g12a)
    g13 = bool(sign and "G13" in sign.upper())

    if is_cycleway or g11 or g12a or g13:
        if g12a:
            speed = 25.0 if profile == "snorfiets" else 30.0
            return AccessDecision(True, profile, "fiets/bromfietspad", speed, speed, "Aangewezen fiets/bromfietspad (G12a of moped=designated).", source, "hoog" if sign or tags.get("moped") else "middel", sign)
        if profile == "bromfiets":
            if _has_any(tags, ["moped"], ALLOW_VALUES):
                return AccessDecision(True, profile, "fiets/bromfietspad", 30, 30, "Bromfiets is expliciet toegestaan op dit pad.", "expliciete OSM-toegangstag", "hoog", sign)
            return AccessDecision(False, profile, "verplicht fietspad", 0, 0, "Bromfiets mag niet op een gewoon verplicht of onverplicht fietspad.", "Rijksoverheid + OSM", "hoog" if g11 or g13 else "middel", sign)
        # Amsterdam's zone decision overrides the national G11 default. OSM
        # commonly records mofa=yes/designated on G11 paths, but inside the
        # zone a snorfiets still belongs on the carriageway unless this is one
        # of the municipality's explicitly listed exception corridors. G12a
        # was handled above and remains open to both profiles.
        if inside_snorfiets_zone and not exception_street:
            return AccessDecision(False, profile, "verplicht fietspad", 0, 0, "Binnen de Amsterdamse zone moet de snorfiets hier naar de rijbaan.", "Gemeente Amsterdam", "hoog" if sign else "middel", sign)
        if highway == "path" and not (g11 or g13 or _has_any(tags, ["mofa", "bicycle"], ALLOW_VALUES)):
            return AccessDecision(None, profile, "onzeker wegvak", 20, 16, "Pad zonder voldoende bord- of toegangsinformatie.", "afleiding uit OSM", "laag", sign)
        reason = "Snorfiets volgt dit fietspad op een Amsterdamse uitzonderingsweg." if exception_street else "Snorfiets mag dit fietspad gebruiken."
        return AccessDecision(True, profile, "verplicht fietspad", 25, 22, reason, "Gemeente Amsterdam + OSM" if exception_street else source, "hoog" if sign or exception_street else "middel", sign)

    if highway in ROAD_HIGHWAYS:
        default_speed = 30.0 if highway in {"living_street", "residential", "service"} else 50.0
        legal_speed = _parse_speed(tags.get("maxspeed"), default_speed)
        vehicle_limit = 25.0 if profile == "snorfiets" else 45.0
        routing_speed = min(legal_speed, vehicle_limit)

        # Amsterdam keeps fiets/bromfietspaden next to its high-speed urban
        # roads. Some OSM carriageway sections (including part of the S103)
        # omit mofa/moped=use_sidepath even though the sidepath is mandatory.
        # Fail closed above 50 km/h unless the vehicle access is explicit: a
        # missing tag must never turn an uncertain 70/80-road into advice.
        if legal_speed > 50 and not _has_any(tags, [key], ALLOW_VALUES):
            return AccessDecision(
                False,
                profile,
                "rijbaan",
                legal_speed,
                0,
                f"Rijbaan van {legal_speed:g} km/u zonder expliciete {key}-toelating; uit voorzorg niet in de route opgenomen.",
                "veiligheidsfilter op basis van OSM-tags",
                "laag",
                sign,
            )

        confidence = "middel"
        reason = "Reguliere rijbaan; geen voertuigspecifiek verbod gevonden."
        if profile == "snorfiets" and inside_snorfiets_zone and not exception_street:
            reason = "Binnen de Amsterdamse snorfietszone hoort de snorfiets op de rijbaan."
            confidence = "hoog"
            source = "Gemeente Amsterdam + OSM"
        elif profile == "snorfiets" and exception_street:
            return AccessDecision(False, profile, "rijbaan", 0, 0, "Op deze Amsterdamse uitzonderingsweg moet de snorfiets het aangewezen pad volgen.", "Gemeente Amsterdam", "hoog", sign)
        return AccessDecision(True, profile, "rijbaan", legal_speed, routing_speed, reason, source, confidence, sign)

    if _has_any(tags, [key], ALLOW_VALUES):
        speed = 25.0 if profile == "snorfiets" else 30.0
        return AccessDecision(True, profile, "onzeker wegvak", speed, speed * 0.8, f"Expliciete {key}-toegang op een ongebruikelijk wegtype.", "expliciete OSM-toegangstag", "middel", sign)

    return AccessDecision(None, profile, "onzeker wegvak", 20, 12, "Wegtype of toegangsinformatie is onvoldoende duidelijk.", "afleiding uit OSM", "laag", sign)
