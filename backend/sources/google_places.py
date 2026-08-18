"""Optional Google Places API (New) source. Only active if
GOOGLE_MAPS_API_KEY is set -- never a required dependency, never HTML
scraping. Requests a narrow FieldMask since Google bills per requested
field group.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx

from backend.models.schemas import RawCompany
from backend.utils.normalize import normalize_domain

logger = logging.getLogger(__name__)

PLACES_BASE = "https://places.googleapis.com/v1"
SEARCH_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.websiteUri,"
    "places.internationalPhoneNumber,places.addressComponents"
)


class GooglePlacesSource:
    source_name = "google_places"

    def __init__(self, api_key: Optional[str] = None, timeout: float = 30.0):
        self.api_key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY")
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _address_component(self, components: list[dict], type_name: str) -> Optional[str]:
        for c in components or []:
            if type_name in (c.get("types") or []):
                return c.get("shortText") or c.get("longText")
        return None

    def search(self, query_terms: list[str], cities: list[str]) -> list[RawCompany]:
        """query_terms x cities, e.g. ["court reporting agency"] x ["Chicago, IL"]."""
        if not self.available:
            logger.info("GOOGLE_MAPS_API_KEY not set -- skipping Google Places source")
            return []

        results: list[RawCompany] = []
        seen_place_ids: set[str] = set()

        for term in query_terms:
            for city in cities:
                text_query = f"{term} in {city}"
                try:
                    resp = httpx.post(
                        f"{PLACES_BASE}/places:searchText",
                        headers={
                            "X-Goog-Api-Key": self.api_key,
                            "X-Goog-FieldMask": SEARCH_FIELD_MASK,
                            "Content-Type": "application/json",
                        },
                        json={"textQuery": text_query},
                        timeout=self.timeout,
                    )
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    logger.warning("Google Places search failed for %r: %s", text_query, exc)
                    continue

                for place in resp.json().get("places", []):
                    place_id = place.get("id")
                    if not place_id or place_id in seen_place_ids:
                        continue
                    seen_place_ids.add(place_id)

                    name = (place.get("displayName") or {}).get("text")
                    if not name:
                        continue
                    website = place.get("websiteUri")
                    components = place.get("addressComponents") or []

                    results.append(
                        RawCompany(
                            company_name=name,
                            website=website,
                            domain=normalize_domain(website) if website else None,
                            phone=place.get("internationalPhoneNumber"),
                            address=place.get("formattedAddress"),
                            city=self._address_component(components, "locality"),
                            state=self._address_component(components, "administrativeAreaLevel1"),
                            country=self._address_component(components, "country") or "US",
                            source=self.source_name,
                            source_url=f"https://www.google.com/maps/place/?q=place_id:{place_id}",
                            source_identifier=place_id,
                        )
                    )
                time.sleep(0.1)  # light self-throttling, not a hard Google requirement

        return results


def collect_for_campaign(campaign) -> list[RawCompany]:
    """Uniform entry point the engine's source dispatcher calls. Cleanly
    returns [] if GOOGLE_MAPS_API_KEY isn't set."""
    src = GooglePlacesSource()
    if not src.available or not campaign.target_cities:
        return []
    return src.search(campaign.keywords[:2], campaign.target_cities)
