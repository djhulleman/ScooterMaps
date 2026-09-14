"""Local browser smoke test; Playwright is only needed by the developer running this file."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
OUT = ROOT / "screenshots"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(EDGE), headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.goto("http://127.0.0.1:8765", wait_until="networkidle")
        page.click("#exampleTrip")
        page.wait_for_selector("#resultPanel:not([hidden])")
        page.wait_for_timeout(500)
        snor_names = page.locator(".tech-segment h3").all_text_contents()
        page.screenshot(path=str(OUT / "desktop-route.png"), full_page=True)

        page.click('[data-group="vehicle"] [data-value="bromfiets"]')
        with page.expect_response(lambda response: response.url.endswith("/api/route"), timeout=180_000):
            page.click("#planRoute")
        page.wait_for_function("!document.querySelector('#planRoute').disabled")
        brom_names = page.locator(".tech-segment h3").all_text_contents()
        brom_details = page.locator(".tech-segment p").all_text_contents()

        page.click('[data-group="vehicle"] [data-value="fiets"]')
        page.click('[data-group="bikeMode"] [data-value="compare"]')
        page.click('[data-group="timeKind"] [data-value="depart"]')
        page.locator("#journeyTime").fill("2026-09-14T11:00")
        with page.expect_response(lambda response: response.url.endswith("/api/journey"), timeout=240_000):
            page.click("#planRoute")
        page.wait_for_function("!document.querySelector('#planRoute').disabled", timeout=240_000)
        bike_options = page.locator(".route-tab.journey").all_text_contents()
        bike_segments = page.locator("#instructionList .instruction").count()
        page.locator(".route-tab.journey", has_text="Fiets parkeren + OV").click()
        parking_visible = page.locator("#parkingResult").is_visible()
        mixed_route_steps = page.locator("#instructionList .instruction").all_text_contents()
        page.screenshot(path=str(OUT / "desktop-bike-compare.png"), full_page=True)

        mobile = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1)
        mobile.on("console", lambda message: console_errors.append(f"mobile: {message.text}") if message.type == "error" else None)
        mobile.goto("http://127.0.0.1:8765", wait_until="networkidle")
        mobile.screenshot(path=str(OUT / "mobile.png"))
        mobile.click("#exampleTrip")
        mobile.wait_for_selector("#resultPanel:not([hidden])")
        mobile.locator("#resultPanel").scroll_into_view_if_needed()
        mobile.wait_for_timeout(400)
        mobile.screenshot(path=str(OUT / "mobile-route.png"))
        mobile.click('[data-group="vehicle"] [data-value="fiets"]')
        mobile.click('[data-group="bikeMode"] [data-value="compare"]')
        mobile.click('[data-group="timeKind"] [data-value="depart"]')
        mobile.locator("#journeyTime").fill("2026-09-14T11:00")
        with mobile.expect_response(lambda response: response.url.endswith("/api/journey"), timeout=240_000):
            mobile.click("#planRoute")
        mobile.wait_for_function("!document.querySelector('#planRoute').disabled", timeout=240_000)
        mobile.locator(".route-tab.journey", has_text="Fiets parkeren + OV").click()
        mobile.locator("#resultPanel").scroll_into_view_if_needed()
        mobile.screenshot(path=str(OUT / "mobile-bike-compare.png"), full_page=True)

        summary = {
            "desktop_route_visible": page.locator(".leaflet-overlay-pane path").count() > 0,
            "mobile_result_visible": mobile.locator("#resultPanel").is_visible(),
            "snorfiets_segmenten": len(snor_names),
            "bromfiets_segmenten": len(brom_names),
            "snorfiets_eerste_wegen": snor_names[:5],
            "bromfiets_eerste_wegen": brom_names[:5],
            "routes_differ": snor_names != brom_names,
            "bromfiets_uses_plain_cyclepath": any(text.strip().startswith("verplicht fietspad") for text in brom_details),
            "bike_option_count": len(bike_options),
            "bike_options": bike_options,
            "bike_segment_count": bike_segments,
            "parking_result_visible": parking_visible,
            "mixed_route_steps": mixed_route_steps,
            "desktop_horizontal_overflow": page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"),
            "mobile_horizontal_overflow": mobile.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"),
            "mobile_bike_result_visible": mobile.locator("#resultPanel").is_visible(),
            "console_errors": console_errors,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        browser.close()


if __name__ == "__main__":
    main()
