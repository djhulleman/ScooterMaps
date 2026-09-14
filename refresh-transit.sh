#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Start eerst ./run.sh zodat de lokale Python-omgeving wordt gemaakt." >&2
  exit 1
fi
.venv/bin/python tools/refresh_transit_data.py
.venv/bin/python tools/refresh_bicycle_parking.py || echo "Waarschuwing: fietsparkeerdata niet vernieuwd; fallback blijft actief."
echo "OV-cache vernieuwd. Herstart ScooterMaps om de nieuwe dienstregeling te laden."
