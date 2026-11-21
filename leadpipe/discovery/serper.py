"""Google Maps results through serper.dev (SERPER_API_KEY in .env; 2,500 free searches, then ~$1 per 1,000).
Better coverage than OpenStreetMap for many trades, with ratings and review counts."""
from __future__ import annotations

import requests

from ..config import Config

URL = "https://google.serper.dev/places"


def discover_serper(cfg: Config, area: str, business_types: list[str], limit: int, pages: int = 3) -> list[dict]:
    key = cfg.env("SERPER_API_KEY")
    if not key:
        raise RuntimeError("SERPER_API_KEY is not set in .env")
    out: list[dict] = []
    for cat in business_types:
        for page in range(1, pages + 1):
            if len(out) >= limit * len(business_types):
                break
            resp = requests.post(URL, json={"q": f"{cat.replace('_', ' ')} in {area}", "page": page}, timeout=30,
                                 headers={"X-API-KEY": key, "Content-Type": "application/json"})
            resp.raise_for_status()
            places = resp.json().get("places") or []
            if not places:
                break
            for p in places:
                out.append({
                    "name": p.get("title"),
                    "category": p.get("category") or cat,
                    "website": p.get("website"),
                    "phone": p.get("phoneNumber"),
                    "address": p.get("address"),
                    "city": area.split(",")[0].strip(),
                    "rating": p.get("rating"),
                    "reviews": p.get("ratingCount"),
                    "lat": p.get("latitude"),
                    "lon": p.get("longitude"),
                    "source": "serper",
                    "source_ref": str(p.get("cid") or p.get("placeId") or f"{p.get('title')}|{p.get('address')}"),
                })
    return out
