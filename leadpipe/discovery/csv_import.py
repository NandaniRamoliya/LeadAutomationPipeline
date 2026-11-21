"""Import leads from a CSV file or a Google Sheet link: your own Google-Maps scrape, Apollo, Sales Navigator,
Clay, Hunter, or any spreadsheet.

Column names are matched loosely (case, spaces, underscores and camelCase don't matter). Recognised:
  business   company | company name | business | business name | place name | title | name (when there's no person column)
  website    website | site | domain | url | company website
  person     contact name | full name | owner | first name + last name | person
  role       role | title (when there's a business column) | job title | position | designation
  email      email | emails | email address | work email
  phone      phone | phone number | mobile | whatsapp number | contact number | phone_number
  socials    instagram | facebook | linkedin | twitter | x | whatsapp
  place      address | city | country | latitude | longitude | google maps url
  quality    rating | total score | stars | reviews | reviews count | rating count
  category   category | category name | industry | type | niche
  priority   priority | tier | value | vip | high value  (yes/1/high/vip -> high-value)
  notes      notes | note | comment
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import requests

from ..config import Config
from ..db import DB
from . import normalize_domain, save_company

ALIASES = {
    "company": ["company", "company name", "organization", "organisation", "business", "business name", "account name",
                "place name", "shop name", "store name", "clinic name", "brand"],
    "website": ["website", "site", "domain", "company website", "website url", "company domain", "web", "homepage"],
    "full_name": ["contact name", "full name", "contact", "person", "owner", "owner name", "decision maker"],
    "first_name": ["first name", "firstname", "first"],
    "last_name": ["last name", "lastname", "last", "surname"],
    "role": ["role", "job title", "position", "designation", "contact title"],
    "email": ["email", "emails", "email address", "work email", "e mail", "contact email", "email 1", "mail"],
    "phone": ["phone", "phone number", "phones", "mobile", "mobile number", "contact number", "telephone", "tel",
              "whatsapp number", "phone 1", "phone unformatted", "international phone"],
    "instagram": ["instagram", "instagram url", "instagram profile", "insta", "ig"],
    "facebook": ["facebook", "facebook url", "facebook page", "fb"],
    "linkedin": ["linkedin", "linkedin url", "linkedin profile", "company linkedin", "person linkedin"],
    "twitter": ["twitter", "x", "twitter url", "x url"],
    "whatsapp": ["whatsapp", "whatsapp link"],
    "address": ["address", "full address", "street", "street address", "location address"],
    "city": ["city", "town", "location", "locality"],
    "country": ["country", "country code"],
    "lat": ["lat", "latitude"],
    "lon": ["lng", "lon", "long", "longitude"],
    "maps_url": ["google maps url", "maps url", "place url", "google maps link", "url"],
    "rating": ["rating", "total score", "stars", "average rating", "score"],
    "reviews": ["reviews", "reviews count", "review count", "rating count", "number of reviews", "user ratings total"],
    "category": ["category", "category name", "industry", "type", "niche", "categories", "main category"],
    "priority": ["priority", "tier", "value", "vip", "high value", "lead quality"],
    "notes": ["notes", "note", "comment", "comments"],
}


def _norm_header(h: str) -> str:
    h = re.sub(r"([a-z])([A-Z])", r"\1 \2", h or "")        # reviewsCount -> reviews Count
    h = re.sub(r"[_\-./]+", " ", h).strip().lower()
    return re.sub(r"\s+", " ", h)


def map_headers(headers: list[str]) -> dict[str, str]:
    norm = {h: _norm_header(h) for h in headers}
    mapping: dict[str, str] = {}
    for field, aliases in ALIASES.items():
        for alias in aliases:  # alias order = preference
            hit = next((h for h, n in norm.items() if n == alias and h not in mapping.values()), None)
            if hit and field not in mapping:
                mapping[field] = hit
    # Map scrapers call the business "title" or "name". "title" is a job title only next to a business column.
    if "company" not in mapping:
        for cand in ("title", "name", "place"):
            hit = next((h for h, n in norm.items() if n == cand and h not in mapping.values()), None)
            if hit:
                mapping["company"] = hit
                break
    else:
        hit = next((h for h, n in norm.items() if n == "title" and h not in mapping.values()), None)
        if hit and "role" not in mapping:
            mapping["role"] = hit
        hit = next((h for h, n in norm.items() if n == "name" and h not in mapping.values()), None)
        if hit and "full_name" not in mapping:
            mapping["full_name"] = hit
    # A "url" column that is a website, not a Maps link
    if "website" not in mapping and "maps_url" in mapping and _norm_header(mapping["maps_url"]) == "url":
        mapping["website"] = mapping.pop("maps_url")
    return mapping


def sheet_csv_url(url: str) -> str | None:
    """Google Sheets share/edit link -> CSV export link (sheet must be shared 'Anyone with the link')."""
    m = re.search(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_\-]+)", url or "")
    if not m:
        return None
    gid = re.search(r"[#&?]gid=(\d+)", url)
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")


def fetch_sheet(url: str) -> str:
    export = sheet_csv_url(url)
    if not export:
        raise ValueError("That isn't a Google Sheets link.")
    resp = requests.get(export, timeout=60, allow_redirects=True)
    if resp.status_code in (401, 403) or "accounts.google.com" in resp.url or resp.text.lstrip().startswith("<"):
        raise ValueError("The sheet isn't public. In Google Sheets: Share → General access → 'Anyone with the link' → Viewer.")
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def _truthy(v: str) -> bool:
    return str(v or "").strip().lower() in ("1", "yes", "y", "true", "high", "vip", "hot", "a", "premium", "high value", "★", "x")


def _num(v: str):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def import_csv(db: DB, cfg: Config, path: str | Path | None = None, text: str | None = None,
               area: str | None = None, priority: bool = False, source: str = "csv") -> dict:
    """Import rows. Returns counts. Re-importing the same rows is safe (duplicates are merged, not added)."""
    from ..channels import parse_phone
    from ..enrich.verify import verify_email
    from ..geo import country_code_for, timezone_for

    if text is None:
        p = str(path or "")
        text = fetch_sheet(p) if "docs.google.com/spreadsheets" in p else Path(p).read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    m = map_headers(reader.fieldnames or [])
    if "company" not in m and "website" not in m:
        raise ValueError(f"Couldn't find a business name or website column. Columns seen: {', '.join(reader.fieldnames or [])}")
    area_country = country_code_for(area.split(",")[-1]) if area and "," in area else None
    stats = {"rows": 0, "companies": 0, "contacts": 0, "updated": 0, "skipped": 0, "columns": m}
    for row in reader:
        stats["rows"] += 1
        g = lambda f: (row.get(m[f]) or "").strip() if f in m else ""  # noqa: E731
        website = g("website")
        name = g("company") or (normalize_domain(website) or "")
        if not name:
            stats["skipped"] += 1
            continue
        if website and "://" not in website and "." in website:
            website = "https://" + website
        country = country_code_for(g("country")) or area_country
        phone = parse_phone(g("phone"), country)
        if not country and phone:
            country = phone.get("country")
        lat, lon = _num(g("lat")), _num(g("lon"))
        tz = timezone_for(country, lat, lon, cfg.get("engine.timezone", "UTC")) if country else None
        rating, reviews = _num(g("rating")), _num(g("reviews"))
        city = g("city") or (area.split(",")[0].strip() if area else None)
        if not city and g("address"):
            parts = [p.strip() for p in g("address").split(",") if p.strip()]
            city = re.sub(r"\d+", "", parts[-2]).strip() if len(parts) >= 2 else None
        data = {
            "name": name, "website": website or None, "phone": (phone or {}).get("e164") or g("phone") or None,
            "address": g("address") or None, "city": city, "country": country, "timezone": tz,
            "category": g("category") or None, "instagram": g("instagram") or None, "facebook": g("facebook") or None,
            "linkedin": g("linkedin") or None, "twitter": g("twitter") or None, "whatsapp": g("whatsapp") or None,
            "rating": rating, "reviews": int(reviews) if reviews is not None else None, "lat": lat, "lon": lon,
            "maps_url": g("maps_url") or None, "priority": priority or _truthy(g("priority")),
            "tags": g("notes") or None, "target": area or source, "source": source,
            "source_ref": f"{name.lower()}|{(normalize_domain(website) or '')}|{(phone or {}).get('e164', '')}",
            "email": g("email"),
        }
        cid = save_company(db, data)
        if cid:
            stats["companies"] += 1
        else:
            stats["updated"] += 1
            dom = normalize_domain(website)
            ex = (db.one("SELECT id FROM companies WHERE domain = ?", (dom,)) if dom else None) or \
                db.one("SELECT id FROM companies WHERE lower(name) = lower(?) ORDER BY id LIMIT 1", (name,))
            cid = ex["id"] if ex else None
        emails = [e.strip().lower() for e in re.split(r"[,;\s]+", g("email")) if "@" in e]
        if cid and emails:
            email = emails[0]
            if db.scalar("SELECT 1 FROM contacts WHERE email = ?", (email,)):
                continue
            first = g("first_name") or (g("full_name").split()[0] if g("full_name") else "")
            full = g("full_name") or " ".join(x for x in (g("first_name"), g("last_name")) if x)
            status = verify_email(email, cfg).status
            if status == "invalid":
                continue
            db.insert("contacts", {"company_id": cid, "full_name": full or None, "first_name": first or None,
                                   "role": g("role") or None, "email": email, "email_status": status,
                                   "email_source": source, "channel": "email", "stage": "new",
                                   "phone": (phone or {}).get("e164"), "linkedin": g("linkedin") or None})
            stats["contacts"] += 1
    db.log("import", f"{source} import: {stats['rows']} rows, {stats['companies']} new businesses, "
           f"{stats['contacts']} contacts, {stats['updated']} already known")
    return stats


def sync_sheets(db: DB, cfg: Config) -> int:
    """Re-import every sheet in sources.google_sheets (new rows only; existing ones are merged)."""
    n = 0
    for item in cfg.get("sources.google_sheets") or []:
        url = item.get("url") if isinstance(item, dict) else str(item)
        if not url:
            continue
        try:
            st = import_csv(db, cfg, url, area=(item.get("area") if isinstance(item, dict) else None),
                            priority=bool(item.get("priority")) if isinstance(item, dict) else False, source="sheet")
            n += st["companies"]
        except Exception as exc:
            db.log("import", f"Google Sheet sync failed ({url[:60]}…): {exc}", level="error")
    return n
