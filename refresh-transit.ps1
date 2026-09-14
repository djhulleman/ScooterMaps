$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Start eerst .\run.ps1 zodat de lokale Python-omgeving wordt gemaakt."
}

Write-Host "ScooterMaps: landelijke GTFS downloaden en Amsterdam-cache vernieuwen..." -ForegroundColor Cyan
& $VenvPython tools\refresh_transit_data.py
if ($LASTEXITCODE -ne 0) {
    throw "Vernieuwen van de OV-cache is mislukt. De bestaande cache is niet overschreven."
}
& $VenvPython tools\refresh_bicycle_parking.py
if ($LASTEXITCODE -ne 0) {
    Write-Warning "OV is vernieuwd, maar OSM-fietsparkeerdata niet. De ingebouwde stationsfallback blijft actief."
}
Write-Host "OV-cache vernieuwd. Herstart ScooterMaps om de nieuwe dienstregeling te laden." -ForegroundColor Green
