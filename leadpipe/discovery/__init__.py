"""Lead Hunter: finds businesses that match your targets.

Providers:
  osm            OpenStreetMap (free, no account): Nominatim to locate the area + Overpass to list businesses
  google_places  Google Places Text Search (needs GOOGLE_PLACES_API_KEY; billed by Google)
  csv            `leadpipe import leads.csv`: bring lists from Apollo, Sales Navigator, Clay, Hunter, etc.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from ..config import Config
from ..db import DB, now_iso, utcnow

log = logging.getLogger("leadpipe.discovery")

USER_AGENT = "LeadAutomationPipeline/1.0 (+https://github.com/NandaniRamoliya/LeadAutomationPipeline)"


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
                "twitter.com", "youtube.com", "linkedin.com", "justdial.com", "google.com", "goo.gl", "maps.app.goo.gl")


def is_social(url: str | None) -> bool:
    d = normalize_domain(url) or ""
    return any(d == h or d.endswith("." + h) for h in SOCIAL_HOSTS)


def save_company(db: DB, data: dict) -> int | None:
    """Insert a company unless we already have it (same source id, or same website domain)."""
    if not data.get("name"):
        return None
    domain = None if is_social(data.get("website")) else normalize_domain(data.get("website"))
    if data.get("source_ref") and db.scalar("SELECT 1 FROM companies WHERE source=? AND source_ref=?",
                                            (data.get("source"), data.get("source_ref"))):
        return None
    if domain and db.scalar("SELECT 1 FROM companies WHERE domain = ?", (domain,)):
        return None
    if not domain and db.scalar("SELECT 1 FROM companies WHERE lower(name)=lower(?) AND coalesce(city,'')=coalesce(?,'')",
                                (data["name"], data.get("city"))):
        return None
    row = {k: data.get(k) for k in ("name", "category", "website", "phone", "address", "city", "country", "instagram",
                                    "facebook", "timezone", "target", "source", "source_ref")}
    row["domain"] = domain
    row["listed_email"] = (data.get("email") or "").split(";")[0].strip().lower() or None
    row["status"] = "new"
    return db.insert("companies", row)


def run_discovery(db: DB, cfg: Config, target_name: str | None = None, limit: int | None = None,
                  force: bool = False) -> int:
    """Run every enabled provider for each target (or one target). Returns number of new companies."""
    from .osm import discover_osm
    from .google_places import discover_google

    if not cfg.get("discovery.enabled", True) and not force:
        return 0
    providers = cfg.get("discovery.providers", ["osm"]) or ["osm"]
    targets = cfg.get("targets", []) or []
    if target_name:
        targets = [t for t in targets if t.get("name") == target_name]
    total = 0
    for t in targets:
        cap = int(limit or t.get("limit", 100))
        for prov in providers:
            try:
                if prov == "osm":
                    found = discover_osm(t, cap)
                elif prov == "google_places":
                    found = discover_google(cfg, t, cap)
                else:
                    log.warning("Unknown provider %s", prov)
                    continue
            except Exception as exc:  # network trouble: try again next cycle
                db.log("discover", f"{prov} failed for {t.get('name')}: {exc}", level="error")
                continue
            new = 0
            for item in found:
                item.setdefault("target", t.get("name"))
                item.setdefault("timezone", t.get("timezone"))
                item.setdefault("country", t.get("country"))
                if save_company(db, item):
                    new += 1
            total += new
            db.log("discover", f"{prov}: {len(found)} businesses found for '{t.get('name')}', {new} new")
    db.kv_set("last_discovery_at", now_iso())
    return total


def discovery_due(db: DB, cfg: Config) -> bool:
    if not cfg.get("discovery.enabled", True):
        return False
    from ..db import parse_iso
    last = parse_iso(db.kv_get("last_discovery_at"))
    hours = float(cfg.get("discovery.interval_hours", 6))
    if last and utcnow() - last < timedelta(hours=hours):
        return False
    ready = db.scalar("""SELECT COUNT(*) FROM companies WHERE status IN ('new','qualified')
                         AND id NOT IN (SELECT company_id FROM contacts WHERE stage != 'new')""") or 0
    return ready < int(cfg.get("discovery.min_ready_leads", 30))


__all__ = ["run_discovery", "discovery_due", "save_company", "normalize_domain", "is_social", "USER_AGENT"]
