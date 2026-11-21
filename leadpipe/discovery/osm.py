"""OpenStreetMap discovery (free). Respects the public servers' usage policies: one request at a
time, an identifying User-Agent, modest result sizes."""
from __future__ import annotations

import time

import requests

from . import USER_AGENT, is_social

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS_SERVERS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]

# Plain-word categories -> OSM tags. Anything else can be given as "key=value".
CATEGORY_TAGS: dict[str, list[str]] = {
    "dentist": ["amenity=dentist", "healthcare=dentist"],
    "clinic": ["amenity=clinic", "healthcare=clinic"],
    "doctor": ["amenity=doctors", "healthcare=doctor"],
    "hospital": ["amenity=hospital"],
    "physiotherapist": ["healthcare=physiotherapist"],
    "pharmacy": ["amenity=pharmacy"],
    "veterinary": ["amenity=veterinary"],
    "cafe": ["amenity=cafe"],
    "restaurant": ["amenity=restaurant"],
    "bakery": ["shop=bakery"],
    "bar": ["amenity=bar", "amenity=pub"],
    "hotel": ["tourism=hotel", "tourism=guest_house"],
    "salon": ["shop=hairdresser", "shop=beauty"],
    "spa": ["leisure=spa", "shop=massage"],
    "gym": ["leisure=fitness_centre"],
    "yoga": ["leisure=fitness_centre][sport=yoga", "sport=yoga"],
    "school": ["amenity=school"],
    "coaching": ["amenity=college", "office=educational_institution", "amenity=language_school", "amenity=driving_school"],
    "lawyer": ["office=lawyer"],
    "accountant": ["office=accountant", "office=tax_advisor"],
    "real_estate": ["office=estate_agent"],
    "insurance": ["office=insurance"],
    "architect": ["office=architect"],
    "it_company": ["office=it", "office=company"],
    "car_repair": ["shop=car_repair"],
    "car_dealer": ["shop=car"],
    "furniture": ["shop=furniture"],
    "clothes": ["shop=clothes", "shop=boutique"],
    "jewellery": ["shop=jewelry"],
    "optician": ["shop=optician"],
    "electronics": ["shop=electronics", "shop=mobile_phone"],
    "florist": ["shop=florist"],
    "photographer": ["craft=photographer", "shop=photo"],
    "plumber": ["craft=plumber"],
    "electrician": ["craft=electrician"],
    "carpenter": ["craft=carpenter"],
    "interior_designer": ["office=interior_design", "shop=interior_decoration"],
    "travel_agency": ["shop=travel_agency"],
    "event_venue": ["amenity=events_venue"],
    "pet_shop": ["shop=pet"],
}


def tags_for(category: str) -> list[str]:
    c = category.strip().lower().replace(" ", "_")
    if "=" in c:
        return [category.strip()]
    return CATEGORY_TAGS.get(c) or CATEGORY_TAGS.get(c.rstrip("s")) or []


def _tag_filter(tag: str) -> str:
    # "a=b" -> ["a"="b"];  supports compound "a=b][c=d"
    parts = [p for p in tag.split("][") if p]
    out = ""
    for p in parts:
        k, _, v = p.partition("=")
        out += f'["{k}"="{v}"]' if v else f'["{k}"]'
    return out


def locate_area(area: str) -> dict | None:
    """Nominatim lookup -> {lat, lon, bbox:(s,w,n,e), country}."""
    resp = requests.get(NOMINATIM, params={"q": area, "format": "json", "limit": 1, "addressdetails": 1}, timeout=30,
                        headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return None
    d = data[0]
    s_, n_, w_, e_ = (float(x) for x in d["boundingbox"])
    # Nominatim returns a point (tiny box) for some cities: widen it to ~12 km around the centre.
    lat, lon = float(d["lat"]), float(d["lon"])
    if (n_ - s_) < 0.05 or (e_ - w_) < 0.05:
        s_, n_, w_, e_ = lat - 0.11, lat + 0.11, lon - 0.13, lon + 0.13
    # Very large boxes (whole states/countries) are clipped to ~60 km around the centre.
    if (n_ - s_) > 1.2 or (e_ - w_) > 1.2:
        s_, n_, w_, e_ = lat - 0.55, lat + 0.55, lon - 0.6, lon + 0.6
    return {"lat": lat, "lon": lon, "bbox": (s_, w_, n_, e_),
            "country": ((d.get("address") or {}).get("country_code") or "").upper(),
            "display_name": d.get("display_name")}


def build_bbox_query(bbox: tuple, categories: list[str], limit: int) -> str:
    s_, w_, n_, e_ = bbox
    scope = f"({s_:.5f},{w_:.5f},{n_:.5f},{e_:.5f})"
    selectors = []
    for cat in categories:
        for tag in tags_for(cat):
            selectors.append(f"nwr{_tag_filter(tag)}[\"name\"]{scope};")
    return f"[out:json][timeout:90];({''.join(selectors)});out center tags {int(limit)};"


def parse_elements(elements: list[dict], category_hint: str, area: str) -> list[dict]:
    out = []
    for el in elements:
        t = el.get("tags", {})
        name = t.get("name:en") if t.get("name:en") and not t.get("name", "").isascii() else t.get("name")
        if not name:
            continue
        website = t.get("website") or t.get("contact:website") or t.get("url") or t.get("brand:website")
        instagram = t.get("contact:instagram") or t.get("instagram")
        facebook = t.get("contact:facebook") or t.get("facebook")
        if website and is_social(website):
            if "instagram" in website:
                instagram = instagram or website
            elif "facebook" in website or "fb.com" in website:
                facebook = facebook or website
        addr = ", ".join(x for x in [t.get("addr:housenumber"), t.get("addr:street"), t.get("addr:suburb"),
                                      t.get("addr:city"), t.get("addr:postcode")] if x)
        category = (t.get("healthcare") or t.get("amenity") or t.get("shop") or t.get("office") or t.get("craft")
                    or t.get("leisure") or t.get("tourism") or category_hint)
        center = el.get("center") or {}
        out.append({
            "name": name.strip(),
            "category": category,
            "website": website,
            "phone": t.get("phone") or t.get("contact:phone") or t.get("contact:mobile") or t.get("mobile"),
            "email": t.get("email") or t.get("contact:email"),
            "address": addr or None,
            "city": t.get("addr:city") or area.split(",")[0].strip(),
            "instagram": instagram,
            "facebook": facebook,
            "linkedin": t.get("contact:linkedin"),
            "twitter": t.get("contact:twitter"),
            "whatsapp": t.get("contact:whatsapp"),
            "lat": el.get("lat") or center.get("lat"),
            "lon": el.get("lon") or center.get("lon"),
            "source": "osm",
            "source_ref": f"{el.get('type')}/{el.get('id')}",
        })
    return out


def search_tile(bbox: tuple, categories: list[str], limit: int, area_label: str) -> list[dict]:
    """One Overpass request for every business type inside one tile of an area."""
    if not categories:
        return []
    query = build_bbox_query(bbox, categories, limit)
    errors: list[str] = []
    for server in OVERPASS_SERVERS:
        try:
            resp = requests.post(server, data={"data": query}, timeout=120, headers={"User-Agent": USER_AGENT})
            if resp.status_code in (429, 504):
                errors.append(f"{server}: busy (HTTP {resp.status_code})")
                time.sleep(10)
                continue
            resp.raise_for_status()
            return parse_elements(resp.json().get("elements", []), categories[0], area_label)
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{server}: {type(exc).__name__}: {str(exc)[:160]}")
            continue
    raise RuntimeError("Overpass servers unavailable. " + " | ".join(errors))
