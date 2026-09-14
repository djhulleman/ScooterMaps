"""Seed the local address cache with public Amsterdam landmarks."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.geocoding import geocode


async def main() -> None:
    for query in ("Leidseplein", "Amstelstation", "Rijksmuseum", "Centraal Station"):
        results, cached = await geocode(query)
        print(f"{query}: {len(results)} resultaat/resultaten ({'cache' if cached else 'opgehaald'})")


if __name__ == "__main__":
    asyncio.run(main())

