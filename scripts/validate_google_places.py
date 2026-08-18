#!/usr/bin/env python3
"""Google Places API (New) validation -- optional source, skipped cleanly
if GOOGLE_MAPS_API_KEY is not set. Never scrapes Google Maps HTML.

Usage:
    python scripts/validate_google_places.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from dotenv import load_dotenv

load_dotenv()

PLACES_BASE = "https://places.googleapis.com/v1"


def main() -> int:
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        print("GOOGLE_MAPS_API_KEY not set -- Google Places is optional and will be skipped.")
        return 0

    print("=== Text Search ===")
    resp = httpx.post(
        f"{PLACES_BASE}/places:searchText",
        headers={
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.formattedAddress,"
                "places.websiteUri,places.internationalPhoneNumber"
            ),
            "Content-Type": "application/json",
        },
        json={"textQuery": "court reporting agency in Chicago"},
        timeout=30.0,
    )
    print(f"status: {resp.status_code}")
    if resp.status_code != 200:
        print(resp.text[:500])
        return 1
    data = resp.json()
    places = data.get("places", [])
    print(f"{len(places)} places returned")
    if not places:
        print("Text Search reachable but returned no results -- check FieldMask/quota.")
        return 0

    first = places[0]
    print(f"sample: {first}")

    print("\n=== Place Details ===")
    place_id = first.get("id")
    resp2 = httpx.get(
        f"{PLACES_BASE}/places/{place_id}",
        headers={
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "id,displayName,formattedAddress,websiteUri,internationalPhoneNumber",
        },
        timeout=30.0,
    )
    print(f"status: {resp2.status_code}")
    print(resp2.text[:500])

    print("\nGoogle Places API (New) validated -- Text Search and Place Details both reachable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
