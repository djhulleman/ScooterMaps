$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "ScooterMaps: lokale Python-omgeving maken..." -ForegroundColor Cyan
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv .venv
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv .venv
    } else {
        throw "Python 3.11 of nieuwer is niet gevonden. Installeer Python via python.org en probeer opnieuw."
    }
}

& $VenvPython -c "import fastapi, uvicorn, networkx, shapely, httpx, osmnx, geopandas" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ScooterMaps: ontbrekende onderdelen installeren (eenmalig)..." -ForegroundColor Cyan
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -r requirements.txt
}

Write-Host "ScooterMaps start op http://127.0.0.1:8765" -ForegroundColor Green
Write-Host "Stoppen kan met Ctrl+C." -ForegroundColor DarkGray
& $VenvPython -m uvicorn app.main:app --host 127.0.0.1 --port 8765

