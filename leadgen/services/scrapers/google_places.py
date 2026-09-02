"""Google Places (New) text search — optional, requires an API key.

Google Places is the highest quality source: it returns the business name,
address components, phone, website, rating and review count in one call, which
removes most of the enrichment work.  It costs money and needs a key, so it is
off unless ``LEADGEN_GOOGLE_MAPS_API_KEY`` is set.
"""

from __future__ import annotations

import logging
import os

import httpx

from ...config import Settings
from .base import BaseScraper, ScrapedLead, clean_text

log = logging.getLogger("leadgen.scrape.places")

ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join(
    [
        "places.displayName",
        "places.formattedAddress",
        "places.addressComponents",
        "places.nationalPhoneNumber",
        "places.internationalPhoneNumber",
        "places.websiteUri",
        "places.rating",
        "places.userRatingCount",
        "places.primaryTypeDisplayName",
        "places.id",
        "nextPageToken",
    ]
)


class GooglePlacesScraper(BaseScraper):
    name = "google_places"
    requires_key = True

    def __init__(self, settings: Settings | None = None):
        super().__init__(settings)
        # Read through Settings like every other knob, so it is configurable in
        # .env and overridable in tests rather than reaching into os.environ.
        self.api_key = self.settings.google_maps_api_key or os.environ.get(
            "LEADGEN_GOOGLE_MAPS_API_KEY", ""
        )
        self.last_error = ""

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, limit: int = 20) -> list[ScrapedLead]:
        self.last_error = ""
        if not self.available:
            self.last_error = "Google Places API key not configured"
            return []
        results: list[ScrapedLead] = []
        page_token = ""
        while len(results) < limit:
            body: dict = {"textQuery": query, "pageSize": min(20, limit)}
            if page_token:
                body["pageToken"] = page_token
            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": FIELD_MASK,
            }
            self.wait_polite()
            try:
                with httpx.Client(timeout=self.settings.scrape_request_timeout) as client:
                    res = client.post(ENDPOINT, json=body, headers=headers)
                    res.raise_for_status()
                    data = res.json()
            except httpx.HTTPError as exc:
                self.last_error = f"Places API error: {exc}"
                break
            for place in data.get("places", []):
                components = place.get("addressComponents", [])
                locality = _component(components, "locality") or _component(
                    components, "postal_town"
                )
                admin = _component(components, "administrative_area_level_1")
                country = _component(components, "country")
                phone = place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber") or ""
                category = (place.get("primaryTypeDisplayName") or {}).get("text", "")
                results.append(
                    ScrapedLead(
                        business_name=clean_text((place.get("displayName") or {}).get("text", ""))[:180],
                        phone=phone,
                        website=place.get("websiteUri", ""),
                        address=clean_text(place.get("formattedAddress", ""))[:300],
                        city=locality,
                        state=admin,
                        country=country,
                        category=category,
                        rating=place.get("rating"),
                        review_count=place.get("userRatingCount"),
                        snippet="",
                        source=self.name,
                        source_url=place.get("websiteUri", ""),
                        raw={"placeId": place.get("id", "")},
                    )
                )
                if len(results) >= limit:
                    break
            page_token = data.get("nextPageToken", "")
            if not page_token:
                break
        if not results and not self.last_error:
            self.last_error = f"No Places results for {query!r}"
        return results


def _component(components: list[dict], kind: str) -> str:
    for comp in components:
        if kind in comp.get("types", []):
            return comp.get("longText") or comp.get("shortText") or ""
    return ""
