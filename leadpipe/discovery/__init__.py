"""Lead Hunter: finds businesses, one area at a time, then moves on to the next area: your start area,
your own list, the rest of your country, then the world (reach.* in config.yaml).

Sources:
  osm            OpenStreetMap (free, no account). Big cities are searched tile by tile.
  serper         Google Maps results via serper.dev (SERPER_API_KEY; 2,500 free searches)
  google_places  Google Places Text Search (GOOGLE_PLACES_API_KEY; billed by Google)
  csv / sheets   `leadpipe import file.csv|<google sheet link>`, plus sheets re-synced every few hours
"""
from __future__ import annotations

import json
import logging
import math
from datetime import timedelta

from ..config import Config
from ..db import DB, now_iso, parse_iso, utcnow

log = logging.getLogger("leadpipe.discovery")

USER_AGENT = "Orbit/1.1 (+https://github.com/NandaniRamoliya/LeadAutomationPipeline)"


def normalize_domain(url: str | None) -> str | None:
    if not url:
        return None
    s = url.strip().lower()
    if "://" not in s:
        s = "http://" + s
    host = s.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


SOCIAL_HOSTS = ("instagram.com", "facebook.com", "fb.com", "linktr.ee", "wa.me", "whatsapp.com", "x.com",
                "twitter.com", "youtube.com", "linkedin.com", "justdial.com", "google.com", "goo.gl", "maps.app.goo.gl",
                "business.site", "sulekha.com", "indiamart.com", "yelp.com", "tripadvisor.com", "zomato.com", "swiggy.com",
                "practo.com")


def is_social(url: str | None) -> bool:
    d = normalize_domain(url) or ""
    return any(d == h or d.endswith("." + h) for h in SOCIAL_HOSTS)


COMPANY_FIELDS = ("name", "category", "website", "phone", "address", "city", "country", "instagram", "facebook",
                  "linkedin", "twitter", "whatsapp", "timezone", "target", "source", "source_ref", "rating", "reviews",
                  "lat", "lon", "area_id", "priority", "tags", "maps_url")


def save_company(db: DB, data: dict) -> int | None:
    """Insert a company unless we already have it (same source id, same website domain, or same name+city).
    Returns the new id, or None if it was a duplicate (high-value flags and missing details are merged in)."""
    if not data.get("name"):
        return None
    domain = None if is_social(data.get("website")) else normalize_domain(data.get("website"))
    existing = None
    if data.get("source_ref"):
        existing = db.one("SELECT * FROM companies WHERE source=? AND source_ref=?", (data.get("source"), data.get("source_ref")))
    if not existing and domain:
        existing = db.one("SELECT * FROM companies WHERE domain = ?", (domain,))
    if not existing:
        existing = db.one("SELECT * FROM companies WHERE lower(name)=lower(?) AND lower(coalesce(city,''))=lower(coalesce(?,''))",
                          (data["name"], data.get("city")))
    if existing:
        merge = {k: data[k] for k in ("phone", "instagram", "facebook", "linkedin", "twitter", "whatsapp", "rating",
                                      "reviews", "website", "maps_url") if data.get(k) and not existing.get(k)}
        if data.get("priority") and not existing.get("priority"):
            merge["priority"] = 1
        if merge:
            db.update("companies", existing["id"], merge)
        return None
    row = {k: data.get(k) for k in COMPANY_FIELDS}
    if row.get("website") and is_social(row["website"]):
        if "instagram" in row["website"]:
            row["instagram"] = row.get("instagram") or row["website"]
        elif "facebook" in row["website"]:
            row["facebook"] = row.get("facebook") or row["website"]
        row["website"] = None
    row["priority"] = 1 if row.get("priority") else 0
    row["domain"] = domain
    row["listed_email"] = (data.get("email") or "").replace(";", ",").split(",")[0].strip().lower() or None
    row["status"] = "new"
    return db.insert("companies", row)


# ---------------------------------------------------------------------------
# Areas: the world rollout
# ---------------------------------------------------------------------------
def _norm_area(name: str) -> str:
    return ", ".join(p.strip() for p in name.split(",") if p.strip())


def seed_areas(db: DB, cfg: Config) -> int:
    """Make sure every configured area exists in order. Safe to call every cycle."""
    from ..geo import COUNTRY_NAMES, DEFAULT_COUNTRY_ORDER, WORLD_CITIES, country_code_for

    r = cfg.section("reach")
    wanted: list[str] = []
    start = _norm_area(str(r.get("start_area") or ""))
    if start:
        wanted.append(start)
    wanted += [_norm_area(a) for a in (r.get("next_areas") or []) if a]
    if r.get("world_rollout", True) and start:
        home = country_code_for(start.split(",")[-1]) if "," in start else None
        order = ([home] if home else []) + [c.upper() for c in (r.get("countries") or DEFAULT_COUNTRY_ORDER)]
        seen_c: list[str] = []
        for code in order:
            if code in seen_c or code not in WORLD_CITIES:
                continue
            seen_c.append(code)
            for city in WORLD_CITIES[code]:
                wanted.append(f"{city}, {COUNTRY_NAMES.get(code, code)}")
    skip = {c.upper() for c in (r.get("skip_countries") or [])} | {c.upper() for c in (cfg.get("compliance.restricted_countries") or [])}
    existing = {a["name"].lower(): a for a in db.all("SELECT * FROM areas")}
    pos = int(db.scalar("SELECT COALESCE(MAX(position), 0) FROM areas") or 0)
    added = 0
    for name in wanted:
        key = name.lower()
        city_only = key.split(",")[0].strip()
        if key in existing or any(k.split(",")[0].strip() == city_only and k.split(",")[-1].strip() == key.split(",")[-1].strip()
                                  for k in existing):
            continue
        code = country_code_for(name.split(",")[-1]) if "," in name else None
        pos += 1
        status = "skipped" if code and code in skip else "pending"
        db.insert("areas", {"name": name, "country": code, "position": pos, "status": status,
                            "note": "country skipped in settings" if status == "skipped" else None})
        existing[key] = {"name": name}
        added += 1
    return added


def add_area(db: DB, name: str, first: bool = True) -> int:
    """Add an area (or move an existing one) to the front of the queue."""
    name = _norm_area(name)
    row = db.one("SELECT * FROM areas WHERE lower(name) = lower(?)", (name,))
    lowest = int(db.scalar("SELECT COALESCE(MIN(position), 0) FROM areas WHERE status IN ('pending','active')") or 0)
    pos = lowest - 1 if first else int(db.scalar("SELECT COALESCE(MAX(position), 0) FROM areas") or 0) + 1
    if row:
        db.update("areas", row["id"], {"position": pos, "status": "pending" if row["status"] in ("skipped", "failed") else row["status"]})
        return row["id"]
    from ..geo import country_code_for
    code = country_code_for(name.split(",")[-1]) if "," in name else None
    return db.insert("areas", {"name": name, "country": code, "position": pos, "status": "pending"})


def current_area(db: DB) -> dict | None:
    """The area being worked on. When it's finished, the next one in line becomes active."""
    act = db.one("SELECT * FROM areas WHERE status = 'active' ORDER BY position LIMIT 1")
    if act:
        return act
    nxt = db.one("SELECT * FROM areas WHERE status = 'pending' ORDER BY position, id LIMIT 1")
    if nxt:
        db.update("areas", nxt["id"], {"status": "active"})
        db.log("discover", f"now reaching out in {nxt['name']}")
        return db.one("SELECT * FROM areas WHERE id = ?", (nxt["id"],))
    return None


def tile_bbox(bbox: tuple, tiles: int, index: int) -> tuple:
    n = max(1, round(math.sqrt(max(tiles, 1))))
    s_, w_, n_, e_ = bbox
    row, col = divmod(index, n)
    dh, dw = (n_ - s_) / n, (e_ - w_) / n
    return (s_ + row * dh, w_ + col * dw, s_ + (row + 1) * dh, w_ + (col + 1) * dw)


def _locate(db: DB, cfg: Config, area: dict) -> dict | None:
    """Geocode once and remember (country, timezone, bounding box)."""
    if area.get("bbox"):
        return area
    from ..geo import timezone_for
    from .osm import locate_area
    info = locate_area(area["name"])
    if not info:
        db.update("areas", area["id"], {"status": "failed", "note": "place not found on the map. Use 'City, Country'"})
        db.log("discover", f"could not find '{area['name']}' on the map; skipping it", level="warn")
        return None
    country = info["country"] or area.get("country")
    tz = timezone_for(country, info["lat"], info["lon"], cfg.get("engine.timezone", "UTC"))
    tiles = max(1, round(math.sqrt(int(cfg.get("reach.tiles_per_area", 9))))) ** 2
    db.update("areas", area["id"], {"lat": info["lat"], "lon": info["lon"], "bbox": json.dumps(info["bbox"]),
                                    "country": country, "timezone": tz, "tiles": tiles})
    return db.one("SELECT * FROM areas WHERE id = ?", (area["id"],))


def run_discovery(db: DB, cfg: Config, area_name: str | None = None, force: bool = False, steps: int = 1) -> int:
    """Search the next piece of the current area (or `area_name`). Returns the number of new businesses."""
    from .google_places import discover_google
    from .osm import search_tile
    from .serper import discover_serper

    seed_areas(db, cfg)
    if area_name:
        add_area(db, area_name, first=True)
        db.execute("UPDATE areas SET status='pending' WHERE status='active' AND lower(name) != lower(?)", (area_name,))
    types = [t for t in (cfg.get("reach.business_types") or []) if t]
    if not types:
        db.log("discover", "no business types set (Settings → Setup → Who to reach)", level="warn")
        return 0
    providers = cfg.get("sources.providers") or ["osm"]
    limit = int(cfg.get("reach.results_per_search", 250))
    total = 0
    for _ in range(max(1, steps)):
        area = current_area(db)
        if not area:
            db.log("discover", "every area in the list is finished. Add more areas or countries in Settings.", level="warn")
            break
        try:
            area = _locate(db, cfg, area)
        except Exception as exc:
            db.log("discover", f"map lookup failed for {area['name']}: {exc}", level="error")
            break
        if not area:
            continue
        bbox = tuple(json.loads(area["bbox"]))
        tiles = int(area["tiles"] or 1)
        idx = int(area["tile"] or 0)
        found: list[dict] = []
        errors = []
        for prov in providers:
            try:
                if prov == "osm":
                    found += search_tile(tile_bbox(bbox, tiles, idx), types, limit, area["name"])
                elif prov == "serper" and idx == 0:
                    found += discover_serper(cfg, area["name"], types, limit)
                elif prov == "google_places" and idx == 0:
                    found += discover_google(cfg, area["name"], types, limit)
            except Exception as exc:
                errors.append(f"{prov}: {exc}")
        if errors and not found:
            db.log("discover", f"{area['name']}: search failed ({'; '.join(errors)[:300]}). Will retry later.", level="error")
            break
        new = 0
        for item in found:
            item.setdefault("timezone", area.get("timezone"))
            item.setdefault("country", area.get("country"))
            item["area_id"] = area["id"]
            item["target"] = area["name"]
            if save_company(db, item):
                new += 1
        total += new
        idx += 1
        values = {"tile": idx, "found": int(area["found"] or 0) + new, "last_run_at": now_iso()}
        if idx >= tiles:
            values["status"] = "done"
            db.log("discover", f"finished {area['name']}: {values['found']} businesses in total. Moving to the next area.")
        db.update("areas", area["id"], values)
        db.log("discover", f"{area['name']} (part {idx}/{tiles}): {len(found)} found, {new} new")
    db.kv_set("last_discovery_at", now_iso())
    return total


def fresh_leads(db: DB) -> int:
    return int(db.scalar("""SELECT COUNT(*) FROM companies co WHERE co.status IN ('new','qualified')
                            AND NOT EXISTS (SELECT 1 FROM contacts ct WHERE ct.company_id = co.id AND ct.stage != 'new')""") or 0)


def discovery_due(db: DB, cfg: Config) -> bool:
    if not cfg.get("reach.auto_discover", True):
        return False
    last = parse_iso(db.kv_get("last_discovery_at"))
    minutes = float(cfg.get("engine.discover_every_minutes", 30))
    if last and utcnow() - last < timedelta(minutes=minutes):
        return False
    return fresh_leads(db) < int(cfg.get("reach.min_ready_leads", 40))


__all__ = ["run_discovery", "discovery_due", "save_company", "normalize_domain", "is_social", "USER_AGENT",
           "seed_areas", "add_area", "current_area", "fresh_leads"]
