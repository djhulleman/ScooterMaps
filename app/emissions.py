from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class EmissionDecision:
    allowed: bool
    reason: str
    confidence: str = "hoog"


def evaluate_emission_access(
    powertrain: str,
    build_year: int,
    exemption: bool = False,
    on_date: date | None = None,
) -> EmissionDecision:
    """Evaluate Amsterdam's brom/snorfiets zero-emission-zone transition rules."""
    on_date = on_date or date.today()
    if exemption:
        return EmissionDecision(True, "Lokale ontheffing is door de gebruiker bevestigd.")
    if powertrain == "electric":
        return EmissionDecision(True, "Elektrische brom- en snorfietsen hebben toegang.")
    if powertrain not in {"fuel", "petrol", "diesel", "gas"}:
        return EmissionDecision(False, "Onbekende aandrijving; toegang kan niet betrouwbaar worden vastgesteld.", "laag")

    if on_date >= date(2030, 1, 1):
        return EmissionDecision(False, "Vanaf 1 januari 2030 hebben alleen uitstootvrije voertuigen toegang.")
    if build_year <= 2010:
        return EmissionDecision(False, "Brandstofvoertuigen uit 2010 of ouder hebben geen toegang.")
    if build_year >= 2025:
        return EmissionDecision(False, "Nieuwe brandstofvoertuigen vanaf 2025 hebben geen toegang.")
    if build_year <= 2017 and on_date >= date(2028, 1, 1):
        return EmissionDecision(False, "De overgangsregeling voor bouwjaar 2011–2017 is verlopen.")
    return EmissionDecision(True, "Voertuig valt binnen de geldende overgangsregeling.")

