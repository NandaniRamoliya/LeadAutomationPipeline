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
    resp = requests.get(NOMINATIM, params={"q": area, "format": "json", "limit": 1}, timeout=30,
                        headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    resp.raise_for_status()
    data = resp.json()
    return data[0] if data else None


def build_query(area_info: dict, categories: list[str], limit: int) -> str:
    selectors = []
    if area_info.get("osm_type") == "relation":
        area_id = 3600000000 + int(area_info["osm_id"])
        scope = "(area.a)"
        header = f"area({area_id})->.a;"
    else:
        s, n, w, e = (float(x) for x in area_info["boundingbox"])
        scope = f"({s},{w},{n},{e})"
        header = ""
    for cat in categories:
        for tag in tags_for(cat):
            selectors.append(f"nwr{_tag_filter(tag)}[\"name\"]{scope};")
    return f"[out:json][timeout:90];{header}({''.join(selectors)});out center tags {int(limit)};"


def parse_elements(elements: list[dict], category_hint: str, area: str) -> list[dict]:
    out = []
    for el in elements:
        t = el.get("tags", {})
        name = t.get("name") or t.get("name:en")
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
        category = (t.get("amenity") or t.get("shop") or t.get("healthcare") or t.get("office") or t.get("craft")
                    or t.get("leisure") or t.get("tourism") or category_hint)
        out.append({
            "name": name.strip(),
            "category": category,
            "website": website,
            "phone": t.get("phone") or t.get("contact:phone") or t.get("contact:mobile"),
            "email": t.get("email") or t.get("contact:email"),
            "address": addr or None,
            "city": t.get("addr:city") or area.split(",")[0].strip(),
            "instagram": instagram,
            "facebook": facebook,
            "source": "osm",
            "source_ref": f"{el.get('type')}/{el.get('id')}",
        })
    return out


def discover_osm(target: dict, limit: int) -> list[dict]:
    area = target.get("area") or ""
    cats = target.get("categories") or []
    if not area or not cats:
        return []
    info = locate_area(area)
    if not info:
        raise RuntimeError(f"OpenStreetMap could not find the area '{area}'. Try 'City, Country'.")
    time.sleep(1.1)  # Nominatim policy: max 1 request/second
    query = build_query(info, cats, limit)
    errors: list[str] = []
    for server in OVERPASS_SERVERS:
        try:
            resp = requests.post(server, data={"data": query}, timeout=120, headers={"User-Agent": USER_AGENT})
            if resp.status_code in (429, 504):
                errors.append(f"{server}: busy (HTTP {resp.status_code})")
                time.sleep(10)
                continue
            resp.raise_for_status()
            return parse_elements(resp.json().get("elements", []), cats[0], area)
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{server}: {type(exc).__name__}: {str(exc)[:160]}")
            continue
    raise RuntimeError("Overpass servers unavailable. " + " | ".join(errors))
