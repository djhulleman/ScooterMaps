from datetime import date

import pytest

from app.emissions import evaluate_emission_access
from app.local_rules import point_in_emission_zone, prohibited_area_at
from app.rules import evaluate_edge_access


def test_g11_differs_between_snorfiets_and_bromfiets():
    tags = {"highway": "cycleway", "traffic_sign": "NL:G11", "osmid": 1}
    assert evaluate_edge_access(tags, "snorfiets").allowed is True
    assert evaluate_edge_access(tags, "bromfiets").allowed is False


@pytest.mark.parametrize("profile", ["snorfiets", "bromfiets"])
def test_g12a_allows_both_profiles(profile):
    decision = evaluate_edge_access(
        {"highway": "cycleway", "traffic_sign": "NL:G12a", "moped": "designated"},
        profile,
    )
    assert decision.allowed is True
    assert decision.place == "fiets/bromfietspad"


@pytest.mark.parametrize("profile", ["snorfiets", "bromfiets"])
def test_regular_road_is_allowed(profile):
    decision = evaluate_edge_access({"highway": "residential", "maxspeed": "30"}, profile)
    assert decision.allowed is True
    assert decision.place == "rijbaan"


@pytest.mark.parametrize(
    ("profile", "tag"),
    [("snorfiets", "mofa"), ("bromfiets", "moped")],
)
def test_30_kmh_road_with_mandatory_sidepath_is_forbidden(profile, tag):
    decision = evaluate_edge_access(
        {"highway": "residential", "maxspeed": "30", tag: "use_sidepath"},
        profile,
    )
    assert decision.allowed is False
    assert decision.place == "rijbaan"
    assert "zijpad" in decision.reason


@pytest.mark.parametrize(
    ("profile", "tag"),
    [("snorfiets", "mofa"), ("bromfiets", "moped")],
)
def test_untagged_70_or_80_kmh_road_fails_closed(profile, tag):
    for speed in (70, 80):
        blocked = evaluate_edge_access(
            {"highway": "secondary", "ref": "S103", "maxspeed": str(speed)},
            profile,
        )
        explicitly_allowed = evaluate_edge_access(
            {"highway": "secondary", "ref": "S103", "maxspeed": str(speed), tag: "yes"},
            profile,
        )
        assert blocked.allowed is False
        assert blocked.routing_speed_kmh == 0
        assert "uit voorzorg" in blocked.reason
        assert explicitly_allowed.allowed is True


def test_oneway_and_vehicle_specific_exception():
    forbidden = evaluate_edge_access({"highway": "residential", "oneway": "yes"}, "bromfiets", reverse_travel=True)
    excepted = evaluate_edge_access(
        {"highway": "residential", "oneway": "yes", "oneway:moped": "no"},
        "bromfiets",
        reverse_travel=True,
    )
    assert forbidden.allowed is False
    assert excepted.allowed is True


@pytest.mark.parametrize("tag,value", [("mofa", "no"), ("access", "no"), ("motor_vehicle", "private")])
def test_explicit_prohibitions(tag, value):
    assert evaluate_edge_access({"highway": "residential", tag: value}, "snorfiets").allowed is False


def test_amsterdam_snorfiets_zone_moves_vehicle_to_road():
    path = evaluate_edge_access(
        {
            "highway": "cycleway",
            "traffic_sign": "NL:G11",
            "mofa": "designated",
            "moped": "no",
        },
        "snorfiets",
        inside_snorfiets_zone=True,
    )
    road = evaluate_edge_access(
        {"highway": "residential"},
        "snorfiets",
        inside_snorfiets_zone=True,
    )
    assert path.allowed is False
    assert road.allowed is True
    assert road.confidence == "hoog"


def test_amsterdam_exception_street_keeps_snorfiets_on_path():
    path = evaluate_edge_access(
        {"highway": "cycleway", "traffic_sign": "NL:G11", "name": "Stadhouderskade"},
        "snorfiets",
        inside_snorfiets_zone=True,
        exception_street=True,
    )
    road = evaluate_edge_access(
        {"highway": "secondary", "name": "Stadhouderskade"},
        "snorfiets",
        inside_snorfiets_zone=True,
        exception_street=True,
    )
    assert path.allowed is True
    assert road.allowed is False


@pytest.mark.parametrize("tags", [{"highway": "motorway"}, {"highway": "primary", "motorroad": "yes"}])
def test_motorway_and_motorroad_are_forbidden(tags):
    assert evaluate_edge_access(tags, "snorfiets").allowed is False
    assert evaluate_edge_access(tags, "bromfiets").allowed is False


def test_ferry_access_and_f9_restriction():
    allowed = evaluate_edge_access({"route": "ferry", "name": "F3", "moped": "yes"}, "bromfiets")
    f9 = evaluate_edge_access({"route": "ferry", "name": "F9 Sporenburg", "moped": "no"}, "bromfiets")
    assert allowed.allowed is True
    assert allowed.place == "pont"
    assert "motor" in allowed.reason.lower()
    assert f9.allowed is False


def test_conflicting_generic_and_specific_sources_prefers_specific_vehicle_tag():
    decision = evaluate_edge_access(
        {"highway": "cycleway", "motor_vehicle": "no", "mofa": "designated", "traffic_sign": "NL:G11"},
        "snorfiets",
    )
    assert decision.allowed is True


def test_permanent_local_override_wins_but_unconfirmed_does_not():
    tags = {"highway": "residential", "osmid": 42}
    permanent = [{"properties": {"osm_id": "42", "vehicle": "snorfiets", "correction": "forbidden_snorfiets", "permanent": True}}]
    pending = [{"properties": {"osm_id": "42", "vehicle": "snorfiets", "correction": "forbidden_snorfiets", "permanent": False}}]
    assert evaluate_edge_access(tags, "snorfiets", overrides=permanent).allowed is False
    assert evaluate_edge_access(tags, "snorfiets", overrides=pending).allowed is True


def test_unknown_path_is_uncertain_not_silently_legal():
    decision = evaluate_edge_access({"highway": "path"}, "snorfiets")
    assert decision.allowed is None
    assert decision.confidence == "laag"


def test_zero_emission_zone_rules_for_2026():
    today = date(2026, 9, 14)
    assert evaluate_emission_access("electric", 2026, on_date=today).allowed is True
    assert evaluate_emission_access("fuel", 2010, on_date=today).allowed is False
    assert evaluate_emission_access("fuel", 2011, on_date=today).allowed is True
    assert evaluate_emission_access("fuel", 2025, on_date=today).allowed is False
    assert evaluate_emission_access("fuel", 2010, exemption=True, on_date=today).allowed is True


def test_zero_emission_transition_dates():
    assert evaluate_emission_access("fuel", 2017, on_date=date(2028, 1, 1)).allowed is False
    assert evaluate_emission_access("fuel", 2020, on_date=date(2029, 1, 1)).allowed is True
    assert evaluate_emission_access("fuel", 2020, on_date=date(2030, 1, 1)).allowed is False


def test_emission_zone_excludes_named_outer_villages():
    assert point_in_emission_zone(4.900, 52.367) is True
    assert point_in_emission_zone(4.995, 52.380) is False  # Durgerdam


def test_local_city_centre_entry_bans_are_configured():
    assert prohibited_area_at(4.8935, 52.3668) == "Muntplein"
