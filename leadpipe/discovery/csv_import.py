"""Import leads from a CSV (exports from Apollo, Sales Navigator, Clay, Hunter, Google Sheets...).

Column names are matched loosely. Recognised columns (any of the aliases):
  company | company name | organization | business      (required)
  website | domain | url | company website
  name | full name | contact | first name + last name
  role | title | job title | position
  email | email address | work email
  phone | city | category | industry | country | instagram | linkedin | notes
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from ..config import Config
from ..db import DB
from . import save_company

ALIASES = {
    "company": ["company", "company name", "organization", "organisation", "business", "business name", "account name"],
    "website": ["website", "domain", "url", "company website", "website url", "company domain"],
    "full_name": ["name", "full name", "contact", "contact name", "person"],
    "first_name": ["first name", "firstname", "first"],
    "last_name": ["last name", "lastname", "last", "surname"],
    "role": ["role", "title", "job title", "position", "designation"],
    "email": ["email", "email address", "work email", "e-mail", "contact email"],
    "phone": ["phone", "phone number", "mobile", "contact phone"],
    "city": ["city", "location", "town"],
    "category": ["category", "industry", "type", "niche"],
    "country": ["country", "country code"],
    "instagram": ["instagram", "instagram url"],
    "notes": ["notes", "note", "comment"],
}


def _map_headers(headers: list[str]) -> dict[str, str]:
    norm = {h: h.strip().lower().replace("_", " ") for h in headers}
    mapping: dict[str, str] = {}
    for field, aliases in ALIASES.items():
        for h, n in norm.items():
            if n in aliases and field not in mapping:
                mapping[field] = h
    return mapping


def import_csv(db: DB, cfg: Config, path: str | Path | None = None, text: str | None = None,
               target: str | None = None) -> dict:
    from ..enrich.verify import verify_email

    if text is None:
        text = Path(path).read_text(encoding="utf-8-sig")  # type: ignore[arg-type]
    reader = csv.DictReader(io.StringIO(text))
    m = _map_headers(reader.fieldnames or [])
    if "company" not in m and "website" not in m:
        raise ValueError("CSV needs at least a 'company' or 'website' column.")
    stats = {"rows": 0, "companies": 0, "contacts": 0, "skipped": 0}
    tgt = next((t for t in cfg.get("targets", []) or [] if t.get("name") == target), {}) if target else {}
    for row in reader:
        stats["rows"] += 1
        g = lambda f: (row.get(m[f]) or "").strip() if f in m else ""  # noqa: E731
        name = g("company") or (g("website").split("//")[-1].split("/")[0] if g("website") else "")
        if not name:
            stats["skipped"] += 1
            continue
        website = g("website")
        if website and "://" not in website:
            website = "https://" + website
        cid = save_company(db, {"name": name, "website": website or None, "phone": g("phone") or None,
                                "city": g("city") or None, "category": g("category") or None,
                                "country": g("country") or tgt.get("country"), "instagram": g("instagram") or None,
                                "timezone": tgt.get("timezone"), "target": target, "source": "csv",
                                "source_ref": f"{name.lower()}|{website.lower()}"})
        if cid:
            stats["companies"] += 1
        else:
            from . import normalize_domain
            dom = normalize_domain(website)
            existing = db.one("SELECT id FROM companies WHERE domain = ?", (dom,)) if dom else \
                db.one("SELECT id FROM companies WHERE lower(name) = lower(?)", (name,))
            cid = existing["id"] if existing else None
        email = g("email").lower()
        if cid and email:
            if db.scalar("SELECT 1 FROM contacts WHERE email = ?", (email,)):
                continue
            first = g("first_name") or (g("full_name").split()[0] if g("full_name") else "")
            full = g("full_name") or " ".join(x for x in (g("first_name"), g("last_name")) if x)
            status = verify_email(email, cfg).status
            db.insert("contacts", {"company_id": cid, "full_name": full or None, "first_name": first or None,
                                   "role": g("role") or None, "email": email, "email_status": status,
                                   "email_source": "csv", "channel": "email", "stage": "new",
                                   "notes": g("notes") or None})
            stats["contacts"] += 1
    db.log("import", f"CSV import: {stats}")
    return stats
