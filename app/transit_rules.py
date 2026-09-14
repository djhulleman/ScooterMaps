from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "transit_rules.json"
AMSTERDAM_TZ = ZoneInfo("Europe/Amsterdam")


@dataclass(frozen=True)
class OnboardDecision:
    allowed: bool
    reason: str
    ticket_name: str | None = None
    supplement_eur: float = 0.0
    capacity_warning: str | None = None
    source: str | None = None
    checked_on: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@lru_cache(maxsize=1)
def load_transit_rules() -> dict[str, Any]:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def _easter(year: int) -> date:
    """Gregorian Easter, Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def dutch_public_holiday(day: date) -> str | None:
    easter = _easter(day.year)
    fixed = {
        date(day.year, 1, 1): "nieuwjaarsdag",
        date(day.year, 4, 27): "koningsdag",
        date(day.year, 5, 5): "bevrijdingsdag",
        date(day.year, 12, 25): "eerste kerstdag",
        date(day.year, 12, 26): "tweede kerstdag",
        easter - timedelta(days=2): "goede vrijdag",
        easter: "eerste paasdag",
        easter + timedelta(days=1): "tweede paasdag",
        easter + timedelta(days=39): "hemelvaartsdag",
        easter + timedelta(days=49): "eerste pinksterdag",
        easter + timedelta(days=50): "tweede pinksterdag",
    }
    return fixed.get(day)


def ensure_amsterdam(value: datetime) -> datetime:
    return value.replace(tzinfo=AMSTERDAM_TZ) if value.tzinfo is None else value.astimezone(AMSTERDAM_TZ)


def _clock(text: str) -> time:
    if text == "24:00":
        return time(23, 59, 59, 999999)
    hour, minute = (int(part) for part in text.split(":"))
    return time(hour, minute)


def _inside_window(moment: datetime, start: str, end: str) -> bool:
    local = ensure_amsterdam(moment).timetz().replace(tzinfo=None)
    return _clock(start) <= local < _clock(end)


def _interval_all_allowed(board: datetime, alight: datetime, windows: list[list[str]]) -> bool:
    """Every instant from boarding until alighting must fall in an allowed window."""
    board = ensure_amsterdam(board)
    alight = ensure_amsterdam(alight)
    cursor = board
    while cursor <= alight:
        if not any(_inside_window(cursor, start, end) for start, end in windows):
            return False
        cursor += timedelta(minutes=1)
    return True


def _interval_hits_blocked(board: datetime, alight: datetime, windows: list[list[str]]) -> bool:
    board = ensure_amsterdam(board)
    alight = ensure_amsterdam(alight)
    cursor = board
    while cursor <= alight:
        if any(_inside_window(cursor, start, end) for start, end in windows):
            return True
        cursor += timedelta(minutes=1)
    return False


def bicycle_onboard_decision(
    agency_id: str,
    route_type: int,
    route_short_name: str,
    product_name: str,
    board: datetime,
    alight: datetime,
) -> OnboardDecision:
    rules = load_transit_rules()
    carrier = rules["carriers"].get(agency_id)
    if not carrier:
        return OnboardDecision(False, "Voor deze vervoerder is geen gecontroleerde fietsregel ingesteld.")
    bike = carrier["ordinary_bicycle"]
    board = ensure_amsterdam(board)
    alight = ensure_amsterdam(alight)

    if agency_id == "GVB":
        matched = next((item for item in bike["allowed"] if int(item["route_type"]) == route_type and (
            not item.get("route_short_name") or str(item["route_short_name"]) == str(route_short_name)
        )), None)
        if not matched:
            if route_type == 3:
                return OnboardDecision(False, "Een gewone fiets mag niet mee in de GVB-bus.", source=bike["source"], checked_on=bike["checked_on"])
            return OnboardDecision(False, "Een gewone fiets mag alleen mee in de metro, tram 26 en op GVB-ponten.", source=bike["source"], checked_on=bike["checked_on"])
        if not matched.get("free"):
            holiday = dutch_public_holiday(board.date())
            weekday_rule = board.weekday() < 5 and not (holiday and bike["blocked_windows_exclude_public_holidays"])
            if weekday_rule and _interval_hits_blocked(board, alight, bike["weekday_blocked_windows"]):
                return OnboardDecision(False, "De rit raakt de GVB-spits (07:00–09:00 of 16:00–18:30).", source=bike["source"], checked_on=bike["checked_on"])
        return OnboardDecision(
            True,
            "Fiets toegestaan op deze GVB-rit; aanwijzingen van personeel blijven leidend.",
            None if matched.get("free") else bike["ticket_name"],
            0.0 if matched.get("free") else float(bike["ticket_price_eur"]),
            bike["capacity"],
            bike["source"],
            bike["checked_on"],
        )

    if agency_id == "IFF:NS":
        if route_type not in {int(item) for item in bike["allowed_route_types"]}:
            return OnboardDecision(False, "Fiets meenemen is voor deze NS-vervoersvorm niet toegestaan.", source=bike["source"], checked_on=bike["checked_on"])
        upper_product = product_name.upper()
        if any(blocked in upper_product for blocked in bike["denied_products"]):
            return OnboardDecision(False, "Dit treinproduct staat geen binnenlandse reis met gewone fiets toe.", source=bike["source"], checked_on=bike["checked_on"])
        holiday = dutch_public_holiday(board.date())
        all_day = board.month in bike["all_day_months"] or (board.weekday() >= 5 and bike["all_day_weekends"])
        if holiday and holiday not in bike["public_holiday_exceptions"] and bike["all_day_public_holidays"]:
            all_day = True
        if not all_day and not _interval_all_allowed(board, alight, bike["weekday_allowed_windows"]):
            return OnboardDecision(False, "De volledige treinrit moet buiten de NS-spits vallen.", source=bike["source"], checked_on=bike["checked_on"])
        return OnboardDecision(True, "Fiets toegestaan met Fietskaart Dal.", bike["ticket_name"], float(bike["ticket_price_eur"]), bike["capacity"], bike["source"], bike["checked_on"])

    return OnboardDecision(False, "Geen fietsregel beschikbaar voor deze vervoerder.")
