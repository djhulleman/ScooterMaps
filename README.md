# ScooterMaps

ScooterMaps is een lokale proof-of-concept routeplanner voor **snorfiets**, **bromfiets**, **fiets** en combinaties van fiets met openbaar vervoer in Amsterdam. De drie wegprofielen blijven juridisch gescheiden. De fietsmodule vergelijkt desgewenst een volledige fietsrit, regulier OV, fiets meenemen, fiets parkeren en OV + OV-fiets als complete deur-tot-deuropties.

> **Let op:** dit is geen gecertificeerd navigatiesysteem of juridisch advies. De actuele verkeersborden en situatie ter plaatse zijn altijd leidend. Een oranje gestreept routeonderdeel betekent dat de brondata onvoldoende of tegenstrijdig zijn.

> **OV-capaciteit:** een berekende rit is geen reservering. GVB en NS kunnen een fiets weigeren wanneer de aangewezen fietsplek vol is; aanwijzingen van personeel zijn altijd leidend.

## Starten op Windows

Vereist: Python 3.11 of nieuwer. Start PowerShell in deze map. Op een standaard Windows-installatie werkt:

```powershell
.\run.ps1
```

Blokkeert Windows lokale scripts met de melding *running scripts is disabled*, gebruik dan deze veilige eenregelige variant. De instelling wordt alleen voor dit proces omzeild en niet permanent gewijzigd:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

Open daarna <http://127.0.0.1:8765>. De eerste start maakt automatisch een virtuele omgeving en installeert ontbrekende onderdelen. De app luistert uitsluitend op `127.0.0.1`.

Op macOS/Linux:

```sh
./run.sh
```

## Wat de app doet

- Route plannen via adressen of twee klikken op de kaart.
- Aparte regels voor snorfiets (`mofa`) en bromfiets (`moped`).
- Snelste legale of rustigere/veiligere legale route.
- Maximaal twee alternatieven naast de aanbevolen route.
- Afstand, reistijd, wisselinstructies en een expliciete pontinstructie.
- Kleurcodering voor rijbaan, verplicht fietspad, fiets/bromfietspad, pont en onzeker wegvak.
- Uitklapbare technische informatie met OSM-tags, verkeersbord, snelheid, reden, bron en betrouwbaarheid.
- Lokaal opgeslagen adrescache en lokale GeoJSON-correcties.
- Datumafhankelijke controle van de Amsterdamse uitstootvrije zone zonder kenteken of persoonsgegevens.
- Een direct bruikbaar demonstratienetwerk terwijl echte Amsterdamse OSM-data wordt voorbereid.
- Een apart fietsprofiel voor gewone fiets of e-bike, snelste of comfortabelere route, fiets-eenrichting, `dismount`, trappen, barrières en ponten.
- Vijf fietsmodi: volledig fietsen, fiets meenemen in OV, parkeren + OV, OV + OV-fiets en alles vergelijken.
- Deur-tot-deursegmenten met fiets-, loop-, wacht-, OV- en overstaptijd, bekende fietstoeslagen en een configureerbare routekostenscore.
- Vertrek nu, vertrek op gekozen tijd of aankomst vóór gekozen tijd, in tijdzone `Europe/Amsterdam`.
- Een lokaal onthouden geparkeerde fiets met terugreis- en wisknop. Een nieuwe gekozen parkeerplek verplaatst de opgeslagen fiets.
- Lokale GTFS-dienstregeling voor GVB en NS met kalenderuitzonderingen, ritten na 24:00 en maximaal drie instelbare overstappen.

Bij een normale start downloadt OSMnx op de achtergrond een Amsterdams netwerk. De voortgang staat in de kaart. Na verwerking komt het in `data/amsterdam_network.pkl.gz`; volgende starts gebruiken deze cache. Als downloaden mislukt, blijft de veilige demonstratiemodus werken en zegt de interface dat nadrukkelijk.

NDW-borden G11, G12 en G13 voor gemeente Amsterdam worden als **validatielaag** gecachet. Ze maken een wegvak in deze PoC niet automatisch legaal of illegaal: zonder betrouwbare richting- en wegvakkoppeling zou dat schijnnauwkeurigheid opleveren.

## Architectuur en lokale data

- `app/bicycle_rules.py` beslist fiets-toegang per OSM-wegvak; scooterregels blijven in `app/rules.py`.
- `app/bicycle_routing.py` bouwt een apart fietsnetwerk en berekent snelle/comfortabele alternatieven.
- `app/transit_data.py` leest de compacte SQLite-cache, verwerkt GTFS-service days en zoekt tijdafhankelijke verbindingen.
- `app/transit_rules.py` past vervoerder-, voertuig-, lijn- en tijdregels toe over het volledige deel dat de fiets aan boord is.
- `app/multimodal.py` zoekt meerdere haltes/stations en maakt de complete toegang-, OV-, overstap- en uitstapketen.
- `config/transit_rules.json` bevat centraal de vervoerdersregels, bedragen, buffers, scoregewichten, controledatum en bronlinks.
- `data/gtfs/amsterdam-transit.sqlite` is de lokale regionale cache. `data/bicycle_parking.json` bevat waar beschikbaar OSM-fietsparkeren; stationsfallbacks blijven beschikbaar. De landelijke download blijft buiten de routerings-hot-path.

De cache opnieuw downloaden en opbouwen gaat met één PowerShell-opdracht:

```powershell
.\refresh-transit.ps1
```

Zonder nieuwe download kan een reeds aanwezig `data/gtfs/gtfs-nl.zip` opnieuw worden verwerkt met:

```powershell
.\.venv\Scripts\python.exe tools\refresh_transit_data.py --offline
```

Een ongeldige of verlopen GTFS-cache wordt zichtbaar gemeld. Fietsroutering blijft dan beschikbaar; OV-modi geven een concrete fout in plaats van een verzonnen verbinding.

## Routeringsregels in het kort

De centrale functie `app.rules.evaluate_edge_access()` retourneert per gericht wegvak: toegestaan (`ja`, `nee` of `onzeker`), profiel, plaats op de weg, wettelijke en toegepaste snelheid, reden, bron, verkeersbord en betrouwbaarheid.

- Expliciete `mofa`/`moped`-tags wegen zwaarder dan generieke afleiding.
- `access`, `vehicle`, `motor_vehicle`, eenrichtingstags, `motorroad` en autosnelwegen worden gecontroleerd.
- Een gewoon fietspad (G11/G13) is geen bromfietsroute; een fiets/bromfietspad (G12a) kan dat wel zijn.
- Binnen de Amsterdamse zone gaat de snorfiets in beginsel naar de rijbaan; de benoemde uitzonderingswegen blijven apart behandeld.
- Lokale inrijverboden en parken worden conservatief uitgesloten.
- Een onbekend pad wordt niet stilzwijgend legaal verklaard, maar als laag-betrouwbaar routeonderdeel getoond.
- Alleen door de gebruiker als permanent bevestigde correcties beïnvloeden latere routes.

### Fiets in OV — gecontroleerde regels

- GVB: gewone fiets buiten de spits in metro en tram 26 met het GVB Fietskaartje van € 2,30 (tarief 2026); op de GVB-pont gratis en zonder spitsvenster.
- GVB: gewone fiets niet in bus of overige trams. Maandag t/m vrijdag wordt 07:00–09:00 en 16:00–18:30 geblokkeerd, behalve op feestdagen.
- NS: gewone fiets/e-bike met Fietskaart Dal van € 8,50 op toegestane binnenlandse treinen. Op werkdagen 09:00–16:00, 18:30–06:30; in weekend, juli/augustus en op feestdagen behalve Koningsdag de hele dag.
- Een rit die een spitsgrens kruist wordt volledig afgewezen: alleen het instapmoment controleren is niet voldoende.
- Een fiets+OV-optie met meer dan 30 minuten wachten wordt niet getoond; kies dan parkeren+OV, volledig fietsen of een ander tijdstip.
- De resultaten scheiden echte haltewachttijd van ingestelde instap-/overstap-/uitstapmarges. Een fietsverbod tijdens de spits wordt als regelblokkade gemeld, niet als “wachten op de metro”.
- ICE, Eurostar, Nightjet, vervangende bus en andere expliciet uitgesloten producten worden niet als fietsverbinding aangeboden.
- OV-fiets kost in 2026 € 4,80 per 24 uur en moet bij dezelfde huurlocatie terug; actuele beschikbaarheid wordt niet gegarandeerd.

## Officiële bronnen

Juridische en databronnen gecontroleerd op **14 september 2026**:

- [Rijksoverheid — regels voor snorfietsen](https://www.rijksoverheid.nl/themas/verkeer-en-vervoer/voertuigen-op-de-weg/bromfiets/snorfiets)
- [Rijksoverheid — regels voor brommers en bromscooters](https://www.rijksoverheid.nl/themas/verkeer-en-vervoer/voertuigen-op-de-weg/bromfiets/brommer)
- [Rijksoverheid — Reglement verkeersregels en verkeerstekens 1990](https://wetten.overheid.nl/BWBR0004825/)
- [Gemeente Amsterdam — parkeer- en inrijverboden voor scooters](https://www.amsterdam.nl/verkeer-vervoer/parkeer-inrijverboden-scooters/)
- [Gemeente Amsterdam — uitstootvrije zone brom- en snorfietsen](https://www.amsterdam.nl/verkeer-vervoer/milieuzones-uitstootvrije-zones/brom-snorfietsen/regels-uitstootvrije-zone-scooters/)
- [Amsterdam Datapunt — machineleesbare uitstootvrije-zonekaart](https://api.data.amsterdam.nl/v1/mvt/uitstootvrije_zones/)
- [Amsterdamse Leidraad CVC — brom- en snorfiets, inclusief uitzonderingswegen](https://bikecity.amsterdam.nl/wp-content/uploads/2021/05/Leidraad_CVC_2020.pdf)
- [GVB — reisregels en voorwaarden, inclusief pont F9](https://gvb.nl/reisinformatie/reisregels-en-voorwaarden)
- [GVB — Fietskaartje en tarieven 2026](https://www.gvb.nl/reisproducten/uur-en-dagkaarten/fietskaartje)
- [NS — fiets in de trein](https://www.ns.nl/reizen/in-de-trein/fiets-in-de-trein)
- [NS — OV-fiets](https://www.ns.nl/producten/deur-tot-deur/ov-fiets)
- [NDOV Loket — open OV-data](https://www2.ndovloket.nl/)
- [GTFS-NL — landelijke statische feed](https://gtfs.ovapi.nl/nl/gtfs-nl.zip)
- [Rijksoverheid — officiële feestdagen](https://www.rijksoverheid.nl/vraag-en-antwoord/arbeidsovereenkomst-en-cao/officiele-feestdagen)
- [NDW — verkeersborden-API](https://docs.ndw.nu/en/data-uitwisseling/interface-beschrijvingen/verkeersborden-api/)
- [Nationaal Wegenbestand](https://nationaalwegenbestand.nl/)
- [OpenStreetMap](https://www.openstreetmap.org/copyright)

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

De unit-tests dekken naast alle bestaande scooterregressies ook fietstoegang, fietseenrichting, afstappen, trappen en barrières; GVB/NS-modi, lijn- en spitsregels; GTFS-tijden na middernacht, kalenders, feestdagen en zomertijd; en de complete fiets-API. De handmatige scootercontroles blijven reproduceerbaar met `python tools/manual_route_checks.py`; metro, tram 26, trein, pont, bus en spitsgrens met `python tools/manual_multimodal_checks.py`.

Laatste lokale verificatie op 14 september 2026:

- 53 geautomatiseerde tests geslaagd.
- Volledige OSM-cache geladen: 64.942 knopen en 157.157 gerichte wegvakken.
- Leidseplein → Amstelstation: snorfiets 6,1 km / 15 min; bromfiets 5,4 km / 10 min. De routes verschillen en het bromfietsprofiel gebruikt geen gewoon fietspad.
- Rustigere bromfietsroute berekend met aparte veiligheidskosten.
- Routes gecontroleerd bij een locatie buiten de A10, Rijksmuseum, Cuyperspassage en Vondelpark.
- IJ-pont, motor-uit-instructie, Amsterdamse zonewissel en uitstootverbod gecontroleerd in het deterministische demonstratienetwerk.
- Adreszoeken en lokale cache gecontroleerd met Leidseplein, Amstelstation, Rijksmuseum en Centraal Station.
- Desktopweergave op 1440 × 1000 en mobiele weergave op 390 × 844 gecontroleerd in Edge; geen browserconsolefouten.

## Bekende beperkingen

- De grenzen in `config/amsterdam_rules.json` zijn leesbare, conservatieve PoC-benaderingen. De officiële uitstootvrije-zonekaart is beschikbaar, maar de geometrie wordt nog niet automatisch naar één lokale beslispolygoon geconverteerd.
- Uitzonderingswegen worden op straatnaam herkend; naamloze of fout benoemde OSM-delen kunnen daardoor onzeker blijven.
- Afslagverboden worden alleen meegenomen voor zover de gerichte OSM-graaf ze representeert; een volledige turn-restriction-router valt buiten deze PoC.
- NDW-borden zijn alleen validatiepunten en nog niet automatisch richtinggevoelig gekoppeld aan OSM-wegvakken.
- De NDW-validatielaag was tijdens de laatste cacheopbouw tijdelijk niet bereikbaar. Dit blokkeert routering niet en wordt niet als impliciete toestemming gebruikt.
- Dienstregeling en wachttijd van ponten worden niet live opgehaald; de route gebruikt een zichtbare schatting via OSM-duurdata of routeringssnelheid.
- De OSM-downloadfilter bevat wegen en paden, maar niet gegarandeerd elke veerverbinding. De meegeleverde demo bevat een toegestane IJ-pont en de verboden F9 als regressievoorbeeld.
- OpenStreetMap-kaarttegels en adreszoeken hebben internet nodig. Routering blijft na een geslaagde netwerkdownload lokaal werken; bij uitval kunnen punten nog steeds op de kaart worden gekozen.
- De GTFS-subset bevat GVB en NS in de regio Amsterdam en is geen landelijke deur-tot-deurplanner. Andere regionale vervoerders zijn nog niet opgenomen.
- GTFS-shapes zijn bewust niet in de compacte cache opgenomen; OV-lijnen worden op de kaart als rechte verbindingen tussen opeenvolgende haltes getekend.
- Reguliere OV-chipkaart/OVpay-tarieven worden niet berekend. Alleen bekende fietsproducten worden opgeteld en als toeslag getoond.
- Stallingcapaciteit, openingstijden en bewaking zijn alleen zichtbaar als de lokale OSM-/fallbackdata ze kennen; onbekende waarden worden expliciet gemeld.
- De planner gebruikt statische GTFS. Vertragingen, uitval, verstoringen en realtime OV-fietsvoorraad vallen buiten deze lokale PoC.

## Beste eerstvolgende verbetering

Voeg GTFS-Realtime als optionele, lokaal gecachete correctielaag toe en koppel officiële stallingscapaciteit/OV-fietsvoorraad. De statische planner blijft dan als veilige fallback bestaan.
