"""Google Places (optional, paid by Google per request). Set GOOGLE_PLACES_API_KEY in .env and add
"google_places" to discovery.providers. Better coverage than OpenStreetMap for many trades."""
from __future__ import annotations

import requests

from ..config import Config

URL = "https://places.googleapis.com/v1/places:searchText"
FIELDS = ("places.id,places.displayName,places.formattedAddress,places.websiteUri,"
          "places.nationalPhoneNumber,places.primaryType")


def discover_google(cfg: Config, target: dict, limit: int) -> list[dict]:
    key = cfg.env("GOOGLE_PLACES_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_PLACES_API_KEY is not set in .env")
    out: list[dict] = []
    for cat in target.get("categories") or []:
        token = None
        while len(out) < limit:
            body = {"textQuery": f"{cat.replace('_', ' ')} in {target.get('area')}", "pageSize": 20}
            if token:
                body["pageToken"] = token
            resp = requests.post(URL, json=body, timeout=30, headers={
                "X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELDS + ",nextPageToken"})
            resp.raise_for_status()
            data = resp.json()
            for p in data.get("places", []):
                out.append({
                    "name": (p.get("displayName") or {}).get("text"),
                    "category": p.get("primaryType") or cat,
                    "website": p.get("websiteUri"),
                    "phone": p.get("nationalPhoneNumber"),
                    "address": p.get("formattedAddress"),
                    "city": (target.get("area") or "").split(",")[0],
                    "source": "google_places",
                    "source_ref": p.get("id"),
                })
            token = data.get("nextPageToken")
            if not token:
                break
    return out[:limit]
