(() => {
  "use strict";
  const SETTINGS_KEY = "scootermaps-bike-settings-v1";
  const PARKED_KEY = "scootermaps-parked-bike-v1";
  const state = { start: null, end: null, pickTarget: "start", profile: "snorfiets", preference: "fast", bikeMode: "bike_all", timeKind: "now", routes: [], selectedRoute: 0, resultKind: "scooter", markers: {}, routeLayer: null, reportFeatures: [], correctionFeature: null };
  const placeColors = { "rijbaan": "#183f6d", "verplicht fietspad": "#00a47a", "fiets/bromfietspad": "#7456d8", "pont": "#168fbd", "onzeker wegvak": "#ee8b17", "fietspad": "#00a47a", "fietsstrook": "#00a47a", "rustige rijbaan": "#2f8d6f", "drukke rijbaan": "#ee8b17", "lopen met fiets": "#6e7479", "gedeeld pad": "#3d9a79" };
  const modeColors = { bike: "#00a47a", bike_walk: "#ee8b17", walk: "#6e7479", metro: "#183f6d", trein: "#563c91", tram: "#e2231a", bus: "#d26e16", pont: "#168fbd", ov: "#183f6d" };
  const modeLabels = { bike: "Fiets", bike_walk: "Lopen met fiets", walk: "Lopen", metro: "Metro", trein: "Trein", tram: "Tram", bus: "Bus", pont: "Pont", ov: "OV" };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
  const formatDistance = meters => meters < 1000 ? `${Math.round(meters)} m` : `${(meters / 1000).toFixed(1).replace(".", ",")} km`;
  const formatClock = value => value ? new Intl.DateTimeFormat("nl-NL", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Amsterdam" }).format(new Date(value)) : "";

  function showToast(message) {
    const toast = $("#toast"); toast.textContent = message; toast.classList.add("show"); clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => toast.classList.remove("show"), 4200);
  }
  if (!window.L) { showToast("De kaartbibliotheek kon niet worden geladen. Controleer je internetverbinding."); return; }

  const map = L.map("map", { zoomControl: false }).setView([52.3676, 4.899], 13);
  L.control.zoom({ position: "topright" }).addTo(map);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; OpenStreetMap-bijdragers" }).addTo(map);
  const markerIcon = label => L.divIcon({ className: `map-marker${label === "B" ? " end" : ""}`, html: label, iconSize: [31, 31] });
  function localDateTimeValue(date = new Date()) { const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000); shifted.setSeconds(0, 0); return shifted.toISOString().slice(0, 16); }
  $("#journeyTime").value = localDateTimeValue(new Date(Date.now() + 30 * 60000));

  function updatePickUi() {
    $$(".pin-button").forEach(button => button.classList.toggle("active", button.dataset.pick === state.pickTarget));
    const label = state.pickTarget === "start" ? "A" : "B";
    $("#pickHint").innerHTML = `<span>${label}</span>Klik je ${state.pickTarget === "start" ? "vertrekpunt" : "bestemming"} op de kaart`;
  }
  function setPoint(kind, lat, lon, label) {
    state[kind] = { lat, lon, label };
    if (state.markers[kind]) map.removeLayer(state.markers[kind]);
    state.markers[kind] = L.marker([lat, lon], { icon: markerIcon(kind === "start" ? "A" : "B"), draggable: true }).addTo(map);
    state.markers[kind].on("dragend", event => { const position = event.target.getLatLng(); state[kind] = { lat: position.lat, lon: position.lng, label: "Punt op de kaart" }; $(`#${kind}Input`).value = `${position.lat.toFixed(5)}, ${position.lng.toFixed(5)}`; });
    $(`#${kind}Input`).value = label || `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    if (kind === "start" && !state.end) state.pickTarget = "end"; updatePickUi();
  }
  map.on("click", event => setPoint(state.pickTarget, event.latlng.lat, event.latlng.lng, "Punt op de kaart"));
  $$(".pin-button").forEach(button => button.addEventListener("click", () => { state.pickTarget = button.dataset.pick; updatePickUi(); }));

  function selectInGroup(group, value) { $$(`[data-group="${group}"] button`).forEach(button => button.classList.toggle("selected", button.dataset.value === value)); }
  function bindChoiceGroup(group, callback) { $$(`[data-group="${group}"] button`).forEach(button => button.addEventListener("click", () => { selectInGroup(group, button.dataset.value); callback(button.dataset.value); })); }
  bindChoiceGroup("vehicle", value => { state.profile = value; updateProfileUi(); });
  bindChoiceGroup("preference", value => { state.preference = value; saveBikeSettings(); });
  bindChoiceGroup("bikeMode", value => { state.bikeMode = value; saveBikeSettings(); });
  bindChoiceGroup("timeKind", value => { state.timeKind = value; $("#journeyTime").disabled = value === "now"; if (value !== "now" && !$("#journeyTime").value) $("#journeyTime").value = localDateTimeValue(); saveBikeSettings(); });

  function updateProfileUi() {
    const bicycle = state.profile === "fiets";
    $$(".bike-only").forEach(item => item.hidden = !bicycle); $("#scooterSettings").hidden = bicycle;
    const preferenceButtons = $$('[data-group="preference"] button');
    preferenceButtons[0].querySelector("strong").textContent = bicycle ? "Snelste" : "Snelst legaal";
    preferenceButtons[1].querySelector("strong").textContent = bicycle ? "Comfortabel" : "Rustiger";
    $("#planRoute span").textContent = bicycle ? "Plan fiets- en OV-route" : "Plan mijn route";
    $$(".bike-legend").forEach(item => item.hidden = !bicycle); updateParkedBikeCard();
  }
  $("#swapPoints").addEventListener("click", () => {
    const formerStart = state.start; const startValue = $("#startInput").value; state.start = state.end; state.end = formerStart;
    $("#startInput").value = $("#endInput").value; $("#endInput").value = startValue;
    if (state.start) setPoint("start", state.start.lat, state.start.lon, $("#startInput").value);
    if (state.end) setPoint("end", state.end.lat, state.end.lon, $("#endInput").value);
  });
  function updateVehicleSummary() { const power = $("#powertrain").value; const year = Number($("#buildYear").value); let text = power === "electric" ? "Elektrisch · toegang" : `Brandstof · ${year}`; if ($("#exemption").checked) text += " · ontheffing"; $("#vehicleSummary").textContent = text; }
  ["powertrain", "buildYear", "exemption"].forEach(id => $(`#${id}`).addEventListener("change", updateVehicleSummary));

  const bikeSettingIds = ["bikeType", "bikeSpeed", "maxAccessBike", "maxEgressBike", "maxTransfers", "boardingBuffer", "transferBuffer", "exitBuffer", "secureParking", "allowTrain", "allowMetro", "allowTram", "allowBus", "allowFerry", "weightDuration", "weightBike", "weightWalk", "weightTransfer", "weightSupplement"];
  function saveBikeSettings() {
    const settings = { bikeMode: state.bikeMode, preference: state.preference, timeKind: state.timeKind };
    bikeSettingIds.forEach(id => settings[id] = $(`#${id}`).type === "checkbox" ? $(`#${id}`).checked : $(`#${id}`).value);
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); updateBikeSummary();
  }
  function loadBikeSettings() {
    try {
      const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
      bikeSettingIds.forEach(id => { if (!(id in saved)) return; const element = $(`#${id}`); if (element.type === "checkbox") element.checked = Boolean(saved[id]); else element.value = saved[id]; });
      if (saved.bikeMode) state.bikeMode = saved.bikeMode; if (saved.preference) state.preference = saved.preference; if (saved.timeKind) state.timeKind = saved.timeKind;
      selectInGroup("bikeMode", state.bikeMode); selectInGroup("preference", state.preference); selectInGroup("timeKind", state.timeKind); $("#journeyTime").disabled = state.timeKind === "now";
    } catch (_) { localStorage.removeItem(SETTINGS_KEY); }
    updateBikeSummary();
  }
  function updateBikeSummary() { const type = $("#bikeType").value === "ebike" ? "E-bike" : "Gewone fiets"; $("#bikeSummary").textContent = `${type} · ${$("#bikeSpeed").value} km/u · max. ${$("#maxTransfers").value} overstap${$("#maxTransfers").value === "1" ? "" : "pen"}`; }
  bikeSettingIds.forEach(id => $(`#${id}`).addEventListener("change", () => { if (id === "bikeType") $("#bikeSpeed").value = $("#bikeType").value === "ebike" ? 22 : 16; saveBikeSettings(); }));

  function bindAddressSearch(kind) {
    const input = $(`#${kind}Input`); const box = $(`#${kind}Suggestions`); let timer;
    input.addEventListener("input", () => {
      clearTimeout(timer); state[kind] = null; const query = input.value.trim(); if (query.length < 3) { box.classList.remove("open"); return; }
      timer = setTimeout(async () => {
        box.innerHTML = '<div class="suggestion">Adres zoeken…</div>'; box.classList.add("open");
        try {
          const response = await fetch(`/api/geocode?q=${encodeURIComponent(query)}`); const payload = await response.json(); if (!response.ok) throw new Error(payload.detail || "Adres zoeken mislukt.");
          box.innerHTML = payload.results.length ? payload.results.map((item, index) => `<button class="suggestion" type="button" data-index="${index}">${escapeHtml(item.label)}</button>`).join("") : '<div class="suggestion">Geen adres gevonden. Kies een punt op de kaart.</div>';
          $$("button", box).forEach(button => button.addEventListener("click", () => { const item = payload.results[Number(button.dataset.index)]; setPoint(kind, item.lat, item.lon, item.label.split(",").slice(0, 2).join(",")); map.flyTo([item.lat, item.lon], 16); box.classList.remove("open"); }));
        } catch (error) { box.innerHTML = `<div class="suggestion">${escapeHtml(error.message)} Kies op de kaart.</div>`; }
      }, 420);
    });
    input.addEventListener("keydown", event => { if (event.key === "Escape") box.classList.remove("open"); });
  }
  bindAddressSearch("start"); bindAddressSearch("end");
  document.addEventListener("click", event => { if (!event.target.closest(".address-field")) $$(".suggestions").forEach(box => box.classList.remove("open")); });
  $("#exampleTrip").addEventListener("click", () => { setPoint("start", 52.3640, 4.8787, "Leidseplein"); setPoint("end", 52.3460, 4.9180, "Amstelstation"); map.fitBounds([[52.343, 4.873], [52.376, 4.924]], { padding: [35, 35] }); planRoute(); });

  function journeyPayload() {
    const time = state.timeKind === "now" ? new Date() : new Date($("#journeyTime").value); if (Number.isNaN(time.getTime())) throw new Error("Kies een geldige reisdatum en tijd.");
    return { start: { lat: state.start.lat, lon: state.start.lon }, end: { lat: state.end.lat, lon: state.end.lon }, mode: state.bikeMode, bicycle_type: $("#bikeType").value, preference: state.preference === "quiet" ? "comfortable" : state.preference, departure_time: time.toISOString(), arrive_by: state.timeKind === "arrive", average_speed_kmh: Number($("#bikeSpeed").value), max_access_bike_km: Number($("#maxAccessBike").value), max_egress_bike_km: Number($("#maxEgressBike").value), max_transfers: Number($("#maxTransfers").value), secure_parking: $("#secureParking").checked, boarding_buffer_min: Number($("#boardingBuffer").value), transfer_buffer_min: Number($("#transferBuffer").value), exit_buffer_min: Number($("#exitBuffer").value), allowed_modes: { train: $("#allowTrain").checked, metro: $("#allowMetro").checked, tram: $("#allowTram").checked, bus: $("#allowBus").checked, ferry: $("#allowFerry").checked }, cost_weights: { duration_min: Number($("#weightDuration").value), bike_km: Number($("#weightBike").value), walk_min: Number($("#weightWalk").value), transfer: Number($("#weightTransfer").value), supplement_eur: Number($("#weightSupplement").value) } };
  }
  async function planRoute() {
    if (!state.start || !state.end) { showToast("Kies eerst vertrek én bestemming — via adres of kaart."); return; }
    const button = $("#planRoute"); const original = button.innerHTML; button.disabled = true; button.innerHTML = "Route berekenen…";
    try {
      const bicycle = state.profile === "fiets";
      const body = bicycle ? journeyPayload() : { start: { lat: state.start.lat, lon: state.start.lon }, end: { lat: state.end.lat, lon: state.end.lon }, vehicle: state.profile, preference: state.preference, powertrain: $("#powertrain").value, build_year: Number($("#buildYear").value), exemption: $("#exemption").checked };
      const response = await fetch(bicycle ? "/api/journey" : "/api/route", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); const payload = await response.json();
      if (!response.ok) throw new Error(Array.isArray(payload.detail) ? "Controleer de route-instellingen." : payload.detail || "Geen route gevonden.");
      state.resultKind = bicycle ? "journey" : "scooter"; state.routes = bicycle ? payload.options : payload.routes; state.selectedRoute = 0;
      renderResults(payload.warnings || []); drawRoutes(); $("#resultPanel").hidden = false; if (window.innerWidth <= 820) $("#resultPanel").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) { showToast(error.message); } finally { button.disabled = false; button.innerHTML = original; }
  }
  $("#planRoute").addEventListener("click", planRoute);

  function renderResults(globalWarnings) {
    const selected = state.routes[state.selectedRoute]; const badge = $("#confidenceBadge"); badge.textContent = `Betrouwbaarheid ${selected.confidence}`; badge.className = `confidence-badge ${selected.confidence}`;
    $("#resultEyebrow").textContent = state.resultKind === "journey" ? "Deur-tot-deur opties" : "Route gevonden";
    if (state.resultKind === "journey") renderJourneyResults(selected, globalWarnings); else renderScooterResults(selected, globalWarnings);
  }
  function compactJourneySteps(option) {
    if (option.type === "bike_all") return [{ mode: "bike", label: "Fiets naar je bestemming", duration_min: option.bike_duration_min, distance_m: option.bike_distance_m }];
    const result = [];
    for (const segment of option.segments) {
      const family = ["bike", "bike_walk"].includes(segment.mode) ? "bike" : segment.mode;
      const phase = segment.label?.startsWith("Naar het OV") ? "access" : segment.label?.startsWith("Vanaf het OV") || segment.label?.startsWith("Met OV-fiets") ? "egress" : "";
      const previous = result.at(-1);
      if (family === "bike" && previous?.family === "bike" && previous.phase === phase) {
        previous.duration_min += segment.duration_min || 0; previous.distance_m += segment.distance_m || 0;
        continue;
      }
      result.push({ ...segment, family, phase, mode: family, label: family === "bike" ? (phase === "access" ? "Fiets naar de opstaphalte" : phase === "egress" ? "Fiets vanaf de uitstaphalte" : "Fietsroute") : segment.label });
    }
    const groupedBikeDistance = result.filter(item => item.family === "bike").reduce((sum, item) => sum + (item.distance_m || 0), 0);
    result.filter(item => item.family === "bike").forEach(item => { item.duration_min = Math.max(1, Math.round(option.bike_duration_min * (item.distance_m || 0) / Math.max(groupedBikeDistance, 1))); });
    return result;
  }
  function renderJourneyResults(selected, globalWarnings) {
    $("#routeTabs").innerHTML = state.routes.map((route, index) => `<button class="route-tab journey ${index === state.selectedRoute ? "selected" : ""}" data-route="${index}" type="button"><strong>${escapeHtml(route.label)}</strong><span class="route-time">${route.duration_min} min</span><small>${formatClock(route.departure)}–${formatClock(route.arrival)} · ${formatDistance(route.bike_distance_m)} fiets · ${route.walk_duration_min} min lopen · ${route.wait_duration_min} min haltewacht · ${route.buffer_duration_min || 0} min marge · ${route.transfers} overstap${route.transfers === 1 ? "" : "pen"} · € ${Number(route.supplement_eur).toFixed(2).replace(".", ",")} toeslag</small>${route.recommended ? '<span class="recommended">Aanbevolen volgens jouw gewichten</span>' : ""}</button>`).join("");
    bindRouteTabs(globalWarnings);
    const ticketNotes = (selected.tickets || []).map(ticket => `${ticket.name} nodig: € ${Number(ticket.price_eur).toFixed(2).replace(".", ",")}.`);
    const warnings = [...globalWarnings, ...(selected.warnings || []), ...ticketNotes, !selected.base_fare_known && selected.cost_note].filter(Boolean); $("#warningList").innerHTML = [...new Set(warnings)].map(message => `<div class="warning">${escapeHtml(message)}</div>`).join("");
    $("#instructionList").innerHTML = compactJourneySteps(selected).map(segment => { const timing = segment.departure ? `${formatClock(segment.departure)}–${formatClock(segment.arrival)}` : `${segment.duration_min || 0} min${segment.distance_m ? ` · ${formatDistance(segment.distance_m)}` : ""}`; const extra = [segment.from && `${segment.from} → ${segment.to}`, segment.platforms?.length && `perron ${segment.platforms.join("/")}`].filter(Boolean).join(" · "); return `<div class="instruction"><i class="mode-icon ${segment.mode}">${escapeHtml((modeLabels[segment.mode] || "OV").slice(0, 1))}</i><span><strong>${escapeHtml(segment.label)}</strong><small>${escapeHtml(timing)}${extra ? ` · ${escapeHtml(extra)}` : ""}</small></span></div>`; }).join("");
    $("#segmentCount").textContent = `${selected.segments.length} reisdelen`;
    $("#technicalList").innerHTML = selected.segments.map(segment => `<article class="tech-segment"><h3><i class="segment-swatch" style="background:${modeColors[segment.mode] || modeColors.ov}"></i>${escapeHtml(segment.label)}</h3><p><strong>${escapeHtml(modeLabels[segment.mode] || segment.mode)}</strong> · ${escapeHtml(segment.details || "Lokale routeberekening")}</p><p>${segment.distance_m ? `${formatDistance(segment.distance_m)} · ` : ""}${segment.departure ? `${formatClock(segment.departure)}–${formatClock(segment.arrival)} · ` : ""}betrouwbaarheid: ${escapeHtml(segment.confidence || "middel")}</p></article>`).join("");
    const parkingBox = $("#parkingResult");
    if (selected.parking) { const p = selected.parking; parkingBox.hidden = false; parkingBox.innerHTML = `<strong>${escapeHtml(p.name)}</strong><small>${p.covered === true ? "Overdekt" : "Overdekking onbekend"} · ${p.secure === true ? "bewaakt/beveiligd" : "beveiliging onbekend"} · capaciteit ${p.capacity ?? "onbekend"} · opening ${escapeHtml(p.opening_hours || "onbekend")}</small><button id="rememberParking" type="button">Onthoud deze parkeerplek</button>`; $("#rememberParking").addEventListener("click", () => { localStorage.setItem(PARKED_KEY, JSON.stringify(p)); updateParkedBikeCard(); showToast("Parkeerplek lokaal onthouden. Een nieuwe plek overschrijft deze."); }); } else parkingBox.hidden = true;
  }
  function renderScooterResults(selected, warnings) {
    $("#routeTabs").innerHTML = state.routes.map((route, index) => `<button class="route-tab ${index === state.selectedRoute ? "selected" : ""}" data-route="${index}" type="button"><strong>${route.duration_min} min</strong><small>${formatDistance(route.distance_m)}${index ? ` · Alt. ${index}` : ""}</small></button>`).join(""); bindRouteTabs(warnings);
    $("#warningList").innerHTML = warnings.map(message => `<div class="warning">${escapeHtml(message)}</div>`).join(""); $("#instructionList").innerHTML = selected.instructions.map((instruction, index) => `<div class="instruction"><i>${instruction.type === "arrival" ? "✓" : index + 1}</i><span>${escapeHtml(instruction.text)}</span></div>`).join("");
    const features = selected.geometry.features; $("#segmentCount").textContent = `${features.length} wegvakken`; $("#parkingResult").hidden = true;
    $("#technicalList").innerHTML = features.map((feature, index) => { const p = feature.properties; return `<article class="tech-segment"><h3><i class="segment-swatch" style="background:${placeColors[p.place] || placeColors['onzeker wegvak']}"></i>${escapeHtml(p.name)} <small>· ${p.length_m} m</small></h3><p><strong>${escapeHtml(p.place)}</strong> · ${escapeHtml(p.reason)}</p><p>Snelheid: wettelijk ${p.legal_speed_kmh} km/u · routering ${p.routing_speed_kmh} km/u · bron: ${escapeHtml(p.source)} · betrouwbaarheid: ${escapeHtml(p.confidence)}</p><p>OSM: ${escapeHtml(JSON.stringify(p.osm_tags))}${p.traffic_sign ? ` · bord ${escapeHtml(p.traffic_sign)}` : ""}</p><button type="button" data-tech-report="${index}">Meld correctie voor dit wegvak</button></article>`; }).join("");
    $$('[data-tech-report]').forEach(button => button.addEventListener("click", () => openCorrection(features[Number(button.dataset.techReport)])));
  }
  function bindRouteTabs(warnings) { $$(".route-tab").forEach(button => button.addEventListener("click", () => { state.selectedRoute = Number(button.dataset.route); renderResults(warnings); drawRoutes(); })); }

  function scooterStyle(feature, selected) { const place = feature.properties.place; const uncertain = feature.properties.confidence === "laag" || place === "onzeker wegvak"; return { color: selected ? (placeColors[place] || placeColors["onzeker wegvak"]) : "#70767a", weight: selected ? 7 : 4, opacity: selected ? .95 : .25, dashArray: uncertain ? "8 7" : (place === "pont" ? "4 7" : null), lineCap: "round", lineJoin: "round" }; }
  function journeyStyle(segment, selected) { const dashed = ["walk", "bike_walk", "pont"].includes(segment.mode); return { color: selected ? (segment.color || modeColors[segment.mode] || modeColors.ov) : "#777c7f", weight: selected ? (segment.mode === "walk" ? 5 : 7) : 3, opacity: selected ? .92 : .18, dashArray: dashed ? (segment.mode === "walk" ? "3 7" : "7 6") : null, lineCap: "round", lineJoin: "round" }; }
  function drawRoutes() {
    if (state.routeLayer) map.removeLayer(state.routeLayer); state.routeLayer = L.layerGroup().addTo(map); state.reportFeatures = [];
    if (state.resultKind === "scooter") {
      state.routes.forEach((route, routeIndex) => { const selected = routeIndex === state.selectedRoute; const layer = L.geoJSON(route.geometry, { style: feature => scooterStyle(feature, selected), onEachFeature: (feature, featureLayer) => { const reportIndex = state.reportFeatures.push(feature) - 1; const p = feature.properties; featureLayer.bindPopup(`<div class="segment-popup"><strong>${escapeHtml(p.name)}</strong><small>${escapeHtml(p.place)} · ${p.length_m} m · betrouwbaarheid ${escapeHtml(p.confidence)}</small><br><small>${escapeHtml(p.reason)}</small><button type="button" data-popup-report="${reportIndex}">Meld een correctie</button></div>`); } }).addTo(state.routeLayer); if (selected) layer.bringToFront(); });
      const bounds = L.geoJSON(state.routes[state.selectedRoute].geometry).getBounds(); if (bounds.isValid()) map.fitBounds(bounds, { padding: [45, 45] }); return;
    }
    let selectedBounds = null;
    state.routes.forEach((route, routeIndex) => route.segments.forEach(segment => { const selected = routeIndex === state.selectedRoute; const layer = L.geoJSON(segment.geometry, { style: () => journeyStyle(segment, selected), onEachFeature: (_, featureLayer) => { const detail = [segment.from && `${segment.from} → ${segment.to}`, segment.departure && `${formatClock(segment.departure)}–${formatClock(segment.arrival)}`, escapeHtml(segment.details || "")].filter(Boolean).join("<br>"); featureLayer.bindPopup(`<div class="segment-popup"><strong>${escapeHtml(segment.label)}</strong><small>${detail}</small></div>`); } }).addTo(state.routeLayer); if (selected) { layer.bringToFront(); selectedBounds = selectedBounds ? selectedBounds.extend(layer.getBounds()) : layer.getBounds(); } }));
    if (selectedBounds?.isValid()) map.fitBounds(selectedBounds, { padding: [45, 45] });
  }

  map.on("popupopen", event => { const button = event.popup.getElement()?.querySelector("[data-popup-report]"); if (button) button.addEventListener("click", () => openCorrection(state.reportFeatures[Number(button.dataset.popupReport)])); });
  function openCorrection(feature) { state.correctionFeature = feature; $("#correctionTitle").textContent = feature.properties.name || "Naamloos wegvak"; $("#correctionNote").value = ""; $("#correctionPermanent").checked = false; $("#correctionType").value = state.profile === "snorfiets" ? "forbidden_snorfiets" : "forbidden_bromfiets"; $("#correctionDialog").showModal(); }
  $("#correctionForm").addEventListener("submit", async event => {
    if (event.submitter?.value === "cancel" || !state.correctionFeature) return; event.preventDefault(); const p = state.correctionFeature.properties; const type = $("#correctionType").value; const vehicle = type.includes("snorfiets") ? "snorfiets" : type.includes("bromfiets") ? "bromfiets" : "both";
    const response = await fetch("/api/overrides", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "Feature", geometry: state.correctionFeature.geometry, properties: { osm_id: p.osm_id, name: p.name, vehicle, correction: type, note: $("#correctionNote").value.trim(), permanent: $("#correctionPermanent").checked, created_at: new Date().toISOString(), place: p.place } }) });
    if (!response.ok) { showToast("Correctie kon niet worden opgeslagen."); return; } $("#correctionDialog").close(); showToast("Correctie lokaal opgeslagen."); loadOverrides();
  });
  async function loadOverrides() {
    const response = await fetch("/api/overrides"); const collection = await response.json(); const box = $("#overrideList");
    if (!collection.features.length) { box.innerHTML = '<div class="empty-overrides">Nog geen lokale correcties.<br>Selecteer een routeonderdeel op de kaart om er één toe te voegen.</div>'; return; }
    box.innerHTML = collection.features.map(feature => { const p = feature.properties; return `<article class="override-card"><h3>${escapeHtml(p.name || "Naamloos wegvak")}</h3><p>${escapeHtml(p.correction)} · ${escapeHtml(p.vehicle)}${p.note ? `<br>${escapeHtml(p.note)}` : ""}</p><div class="override-actions"><button type="button" data-toggle="${escapeHtml(p.id)}">${p.permanent ? "Niet automatisch toepassen" : "Bevestig als permanent"}</button><button class="delete" type="button" data-delete="${escapeHtml(p.id)}">Verwijderen</button></div></article>`; }).join("");
    $$('[data-toggle]', box).forEach(button => button.addEventListener("click", async () => { const feature = collection.features.find(item => item.properties.id === button.dataset.toggle); await fetch(`/api/overrides/${encodeURIComponent(button.dataset.toggle)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ permanent: !feature.properties.permanent }) }); loadOverrides(); }));
    $$('[data-delete]', box).forEach(button => button.addEventListener("click", async () => { await fetch(`/api/overrides/${encodeURIComponent(button.dataset.delete)}`, { method: "DELETE" }); loadOverrides(); }));
  }
  function setDrawer(open) { $("#overrideDrawer").classList.toggle("open", open); $("#overrideDrawer").setAttribute("aria-hidden", String(!open)); $("#drawerBackdrop").hidden = !open; if (open) loadOverrides(); }
  $("#openOverrides").addEventListener("click", () => setDrawer(true)); $("#closeOverrides").addEventListener("click", () => setDrawer(false)); $("#drawerBackdrop").addEventListener("click", () => setDrawer(false));
  $(".legend-toggle").addEventListener("click", event => { const collapsed = $("#legend").classList.toggle("collapsed"); event.currentTarget.setAttribute("aria-expanded", String(!collapsed)); $("span", event.currentTarget).textContent = collapsed ? "+" : "−"; });

  function parkedBike() { try { return JSON.parse(localStorage.getItem(PARKED_KEY) || "null"); } catch (_) { return null; } }
  function updateParkedBikeCard() { const parking = parkedBike(); const visible = state.profile === "fiets" && parking; $("#parkedBikeCard").hidden = !visible; if (visible) $("#parkedBikeText").textContent = `${parking.name} · ${Number(parking.lat).toFixed(4)}, ${Number(parking.lon).toFixed(4)}`; }
  $("#clearParkedBike").addEventListener("click", () => { localStorage.removeItem(PARKED_KEY); updateParkedBikeCard(); showToast("Geparkeerde fiets gewist."); });
  $("#returnToBike").addEventListener("click", () => { const parking = parkedBike(); if (!parking) return; if (state.end) setPoint("start", state.end.lat, state.end.lon, $("#endInput").value || "Huidige bestemming"); setPoint("end", Number(parking.lat), Number(parking.lon), parking.name); state.bikeMode = "return_to_bike"; planRoute(); });

  async function refreshStatus() {
    try {
      const response = await fetch("/api/status"); const status = await response.json(); const network = status.network || status; const transit = status.transit || { state: "missing" }; const chip = $("#dataChip"); chip.classList.toggle("ready", network.state === "ready" && (network.mode === "osm" || transit.state === "ready"));
      $("span:last-child", chip).textContent = `${network.mode === "osm" ? `OSM · ${network.data_date}` : "Demo-kaart"}${transit.state === "ready" ? " · OV lokaal" : " · geen OV-cache"}`;
      const banner = $("#dataBanner"); const show = network.state === "preparing" || network.state === "warning" || network.mode === "demo" || transit.state !== "ready"; banner.hidden = !show;
      $("#dataMessage").textContent = network.state === "preparing" ? network.message : transit.state !== "ready" ? transit.message : network.message; $("#dataPercent").textContent = network.state === "preparing" ? `${network.progress}%` : network.mode === "demo" ? "DEMO" : transit.state !== "ready" ? "OV" : ""; $("#dataProgress").style.width = `${network.progress || 0}%`; $("#retryData").hidden = network.state !== "warning"; if (network.state === "preparing") setTimeout(refreshStatus, 1800);
    } catch (_) { $("#dataChip span:last-child").textContent = "Backend niet bereikbaar"; }
  }
  $("#retryData").addEventListener("click", async () => { await fetch("/api/prepare", { method: "POST" }); refreshStatus(); });
  loadBikeSettings(); updatePickUi(); updateVehicleSummary(); updateProfileUi(); refreshStatus();
})();
