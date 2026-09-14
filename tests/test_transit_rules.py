from datetime import datetime

from app.transit_rules import AMSTERDAM_TZ, bicycle_onboard_decision, dutch_public_holiday


def moment(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=AMSTERDAM_TZ)


def test_gvb_metro_and_tram_26_allowed_offpeak_but_bus_and_other_tram_are_not():
    assert bicycle_onboard_decision("GVB", 1, "52", "", moment(2026, 9, 14, 11), moment(2026, 9, 14, 11, 15)).allowed
    assert bicycle_onboard_decision("GVB", 0, "26", "", moment(2026, 9, 14, 11), moment(2026, 9, 14, 11, 15)).allowed
    assert not bicycle_onboard_decision("GVB", 0, "12", "", moment(2026, 9, 14, 11), moment(2026, 9, 14, 11, 15)).allowed
    assert not bicycle_onboard_decision("GVB", 3, "22", "", moment(2026, 9, 14, 11), moment(2026, 9, 14, 11, 15)).allowed


def test_full_onboard_interval_may_not_cross_gvb_peak_boundary():
    decision = bicycle_onboard_decision("GVB", 1, "52", "", moment(2026, 9, 14, 6, 55), moment(2026, 9, 14, 7, 5))
    assert not decision.allowed


def test_gvb_weekend_and_public_holiday_are_all_day():
    assert bicycle_onboard_decision("GVB", 1, "52", "", moment(2026, 9, 13, 8), moment(2026, 9, 13, 8, 15)).allowed
    assert dutch_public_holiday(moment(2026, 5, 5, 8).date()) == "bevrijdingsdag"
    assert bicycle_onboard_decision("GVB", 1, "52", "", moment(2026, 5, 5, 8), moment(2026, 5, 5, 8, 15)).allowed


def test_ns_entire_trip_must_fit_daluren_and_international_products_are_denied():
    assert bicycle_onboard_decision("IFF:NS", 2, "Intercity", "Intercity", moment(2026, 9, 14, 10), moment(2026, 9, 14, 11)).allowed
    assert not bicycle_onboard_decision("IFF:NS", 2, "Intercity", "Intercity", moment(2026, 9, 14, 15, 55), moment(2026, 9, 14, 16, 5)).allowed
    assert bicycle_onboard_decision("IFF:NS", 2, "Intercity", "Intercity", moment(2026, 7, 6, 8), moment(2026, 7, 6, 9)).allowed
    assert not bicycle_onboard_decision("IFF:NS", 2, "ICE", "ICE International", moment(2026, 9, 14, 11), moment(2026, 9, 14, 12)).allowed


def test_ns_ticket_price_is_current_configured_price():
    decision = bicycle_onboard_decision("IFF:NS", 2, "Sprinter", "Sprinter", moment(2026, 9, 14, 11), moment(2026, 9, 14, 12))
    assert decision.supplement_eur == 8.50
    assert decision.checked_on == "2026-09-14"
