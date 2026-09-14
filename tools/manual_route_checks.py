"""Reproduceerbare handmatige route- en locatieregels voor de Amsterdamse PoC."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.data_manager import network_manager
from app.demo_network import build_demo_graph
from app.local_rules import point_in_snorfiets_zone, prohibited_area_at
from app.routing import VehicleSettings, calculate_routes


def check(name: str, condition: bool, detail: str = "") -> None:
    marker = "PASS" if condition else "FAIL"
    print(f"{marker:4}  {name}{': ' + detail if detail else ''}")
    if not condition:
        raise AssertionError(name)


def edge_names(payload: dict) -> list[str]:
    return [feature["properties"]["name"] for feature in payload["routes"][0]["geometry"]["features"]]


def main() -> None:
    network_manager._graph = build_demo_graph()
    network_manager._mode = "demo"
    network_manager._status.update({"mode": "demo", "state": "ready"})

    inside = point_in_snorfiets_zone(4.9000, 52.3670)
    outside = not point_in_snorfiets_zone(4.7500, 52.4200)
    check("Locatie binnen A10/snorfietszone", inside)
    check("Locatie buiten A10/snorfietszone", outside)
    check("Rijksmuseumpassage wordt herkend", prohibited_area_at(4.8854, 52.3600) == "Rijksmuseumpassage")
    check("Cuyperspassage wordt herkend", prohibited_area_at(4.9002, 52.3788) == "Cuyperspassage")
    check("Vondelpark wordt herkend", prohibited_area_at(4.8660, 52.3570) == "Vondelpark")

    start, end = (52.3640, 4.8787), (52.3460, 4.9180)
    snor = calculate_routes(start, end, VehicleSettings("snorfiets"), "fast")
    brom = calculate_routes(start, end, VehicleSettings("bromfiets"), "fast")
    snor_names, brom_names = edge_names(snor), edge_names(brom)
    check("Snor- en bromfietsroute verschillen", snor_names != brom_names, f"snor={snor_names}; brom={brom_names}")
    check("Bromfiets vermijdt gewoon fietspad", all(feature["properties"]["place"] != "verplicht fietspad" for feature in brom["routes"][0]["geometry"]["features"]))
    transitions = [item["text"] for item in snor["routes"][0]["instructions"]]
    check("Overgang/plaats-op-de-weg wordt geïnstrueerd", any("fietspad" in item for item in transitions), str(transitions))

    ferry = calculate_routes((52.3795, 4.9000), (52.3920, 4.9050), VehicleSettings("snorfiets"), "fast")
    ferry_instructions = [item["text"] for item in ferry["routes"][0]["instructions"]]
    check("IJ-pont met motor-uit-instructie", any("motor uit" in item.lower() for item in ferry_instructions), str(ferry_instructions))

    try:
        calculate_routes(start, end, VehicleSettings("snorfiets", "fuel", 2010), "fast")
    except ValueError as exc:
        check("Uitstootvrije zone blokkeert bouwjaar 2010", "2010" in str(exc), str(exc))
    else:
        check("Uitstootvrije zone blokkeert bouwjaar 2010", False)


if __name__ == "__main__":
    main()
