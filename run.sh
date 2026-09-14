#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  printf '%s\n' 'ScooterMaps: lokale Python-omgeving maken...'
  python3 -m venv .venv
fi

if ! .venv/bin/python -c 'import fastapi, uvicorn, networkx, shapely, httpx, osmnx, geopandas' 2>/dev/null; then
  printf '%s\n' 'ScooterMaps: ontbrekende onderdelen installeren (eenmalig)...'
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

printf '%s\n' 'ScooterMaps start op http://127.0.0.1:8765'
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765

