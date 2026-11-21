"""Google Places (optional, paid by Google per request). Set GOOGLE_PLACES_API_KEY in .env and add
"google_places" to sources.providers. Best coverage, with ratings and review counts."""
from __future__ import annotations

import requests

from ..config import Config

URL = "https://places.googleapis.com/v1/places:searchText"
FIELDS = ("places.id,places.displayName,places.formattedAddress,places.websiteUri,places.nationalPhoneNumber,"
          "places.internationalPhoneNumber,places.primaryType,places.rating,places.userRatingCount,places.location,"
          "places.googleMapsUri")


def discover_google(cfg: Config, area: str, business_types: list[str], limit: int) -> list[dict]:
    key = cfg.env("GOOGLE_PLACES_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_PLACES_API_KEY is not set in .env")
    out: list[dict] = []
    for cat in business_types:
        token = None
        got = 0
        while got < limit:
            body = {"textQuery": f"{cat.replace('_', ' ')} in {area}", "pageSize": 20}
            if token:
                body["pageToken"] = token
            resp = requests.post(URL, json=body, timeout=30, headers={
                "X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELDS + ",nextPageToken"})
            resp.raise_for_status()
            data = resp.json()
            for p in data.get("places", []):
                loc = p.get("location") or {}
                out.append({
                    "name": (p.get("displayName") or {}).get("text"),
                    "category": p.get("primaryType") or cat,
                    "website": p.get("websiteUri"),
                    "phone": p.get("internationalPhoneNumber") or p.get("nationalPhoneNumber"),
                    "address": p.get("formattedAddress"),
                    "city": area.split(",")[0].strip(),
                    "rating": p.get("rating"),
                    "reviews": p.get("userRatingCount"),
                    "lat": loc.get("latitude"),
                    "lon": loc.get("longitude"),
                    "maps_url": p.get("googleMapsUri"),
                    "source": "google_places",
                    "source_ref": p.get("id"),
                })
                got += 1
            token = data.get("nextPageToken")
            if not token:
                break
    return out
