"""Research job: audit each new company, find the right people + emails, verify, score, qualify."""
from __future__ import annotations

import json
import logging

from ..config import Config
from ..db import DB, now_iso
from ..llm import LLM
from ..pipeline import is_suppressed
from ..templates import SIGNALS
from .verify import is_sendable, verify_email
from .website import SiteAudit, audit_website

log = logging.getLogger("leadpipe.enrich")

FREE_MAIL = {"gmail.com", "yahoo.com", "yahoo.co.in", "hotmail.com", "outlook.com", "live.com", "icloud.com",
             "rediffmail.com", "aol.com", "proton.me", "protonmail.com", "zoho.com", "gmx.com"}
DECISION_ROLES = ("founder", "owner", "ceo", "director", "principal", "proprietor", "partner", "doctor", "manager")

AUDIT_SYSTEM = ("You are a careful website auditor working for a freelancer. From the verified data only, write the 2 "
                "most important, specific problems this business has that the freelancer's service fixes, in plain "
                "words a business owner understands (max 14 words each). Do not invent anything; if unsure, say less.")
AUDIT_SCHEMA = {"type": "object", "properties": {"problems": {"type": "array", "items": {"type": "string"}}},
                "required": ["problems"], "additionalProperties": False}


def score_company(cfg: Config, signals: dict, has_named_person: bool, best_status: str | None) -> float:
    weights = cfg.section("signals")
    score = sum(float(weights.get(k, 0) or 0) for k in signals)
    if has_named_person:
        score += 10
    if best_status == "valid":
        score += 5
    return round(min(score, 100.0), 1)


def _rank_emails(emails: list[str], domain: str | None, people: list[dict]) -> list[tuple[str, dict | None]]:
    """Personal same-domain emails matched to a person first, then other same-domain, then free-mail."""
    ranked: list[tuple[int, str, dict | None]] = []
    for e in emails:
        local, _, edom = e.partition("@")
        if domain and edom != domain and not edom.endswith("." + domain) and edom not in FREE_MAIL:
            continue  # someone else's address (web agency, plugin vendor...)
        person = None
        for p in people:
            first = p["full_name"].replace("Dr. ", "").replace("Dr ", "").split()[0].lower()
            if len(first) > 2 and first in local:
                person = p
                break
        rank = 0 if person else (2 if local in ("info", "contact", "hello", "office") else 1)
        if edom in FREE_MAIL:
            rank += 1
        ranked.append((rank, e, person))
    ranked.sort(key=lambda r: r[0])
    return [(e, p) for _, e, p in ranked]


def enrich_company(db: DB, cfg: Config, company: dict, llm: LLM | None = None,
                   audit: SiteAudit | None = None) -> dict:
    ecfg = cfg.section("enrichment")
    if audit is None:
        site = company.get("website")
        if not site and company.get("instagram"):
            site = company["instagram"]
        audit = audit_website(site, timeout=float(ecfg.get("request_timeout", 12)),
                              max_pages=int(ecfg.get("max_pages_per_site", 5)))
    signals = dict(audit.signals)
    domain = company.get("domain")

    candidates = list(audit.emails)
    if company.get("listed_email"):
        candidates.insert(0, company["listed_email"])
    ranked = _rank_emails(candidates, domain, audit.people)

    max_contacts = int(ecfg.get("max_contacts_per_company", 2))
    created: list[int] = []
    best_status = None
    for email, person in ranked:
        if len(created) >= max_contacts:
            break
        if is_suppressed(db, email) or db.scalar("SELECT 1 FROM contacts WHERE email = ?", (email,)):
            continue
        v = verify_email(email, cfg)
        if v.status == "invalid":
            continue
        best_status = best_status or v.status
        person = person or (audit.people[0] if audit.people and not created and v.status == "risky" else None)
        full = person["full_name"] if person else None
        first = full.replace("Dr. ", "Dr ").split()[0] if full else None
        if first == "Dr" and full:
            first = "Dr " + full.split()[-1]
        created.append(db.insert("contacts", {
            "company_id": company["id"], "full_name": full, "first_name": first,
            "role": person["role"] if person else None, "email": email, "email_status": v.status,
            "email_source": "website" if email in audit.emails else "listing", "channel": "email", "stage": "new",
            "notes": v.reason}))

    # Guessed address for a named decision-maker when nothing else was found
    if not created and audit.people and domain and ecfg.get("allow_guessed_emails"):
        p = audit.people[0]
        first = p["full_name"].replace("Dr. ", "").split()[0].lower()
        guess = f"{first}@{domain}"
        v = verify_email(guess, cfg, guessed=True)
        if v.status != "invalid" and not db.scalar("SELECT 1 FROM contacts WHERE email = ?", (guess,)):
            best_status = v.status
            created.append(db.insert("contacts", {
                "company_id": company["id"], "full_name": p["full_name"], "first_name": p["full_name"].split()[0],
                "role": p["role"], "email": guess, "email_status": v.status, "email_source": "guess",
                "channel": "email", "stage": "new", "notes": v.reason}))

    # Also count contacts that existed already (e.g. from a CSV import)
    existing = db.all("SELECT * FROM contacts WHERE company_id = ?", (company["id"],))
    sendable = [c for c in existing if c["channel"] == "email" and is_sendable(c["email_status"], cfg)]
    if not best_status and sendable:
        best_status = sendable[0]["email_status"]
    has_person = any(c.get("full_name") for c in existing)
    score = score_company(cfg, signals, has_person, best_status)

    # Manual channel (phone / Instagram DM) when there is no usable email
    if not sendable and ecfg.get("manual_outreach", True) and (company.get("phone") or company.get("instagram")) \
            and not any(c["channel"] == "manual" for c in existing):
        db.insert("contacts", {"company_id": company["id"], "channel": "manual", "stage": "new",
                               "email_status": "none", "notes": "no email found: reach by phone / Instagram DM"})
        existing = db.all("SELECT * FROM contacts WHERE company_id = ?", (company["id"],))

    problems = [SIGNALS[k]["problem"] for k in sorted(signals, key=lambda k: -float(cfg.get(f"signals.{k}", 0) or 0))
                if k in SIGNALS and float(cfg.get(f"signals.{k}", 0) or 0) > 0][:3]
    llm = llm if llm is not None else None
    if llm and llm.available and score >= float(ecfg.get("min_score", 25)) and audit.ok:
        out = llm.json(AUDIT_SYSTEM, json.dumps({
            "freelancer_service": cfg.get("profile.service_long"), "business": company.get("name"),
            "category": company.get("category"), "verified_signals": signals, "site_text": audit.summary[:1200]},
            ensure_ascii=False, default=str), AUDIT_SCHEMA, effort=str(cfg.get("llm.effort_classify", "low")))
        if out and out.get("problems"):
            problems = [str(p)[:140] for p in out["problems"][:2]] + problems[:1]

    restricted = {c.upper() for c in (cfg.get("compliance.restricted_countries") or [])}
    reachable = sendable or any(c["channel"] == "manual" for c in existing)
    if (company.get("country") or "").upper() in restricted:
        status, reason = "disqualified", f"country {company.get('country')} is restricted in compliance settings"
    elif not reachable:
        status, reason = "disqualified", "no usable email, phone or Instagram"
    elif score < float(ecfg.get("min_score", 25)):
        status, reason = "disqualified", f"score {score} below min_score"
    else:
        status, reason = "qualified", f"score {score}"
    if status == "disqualified":
        for c in existing:
            if c["stage"] == "new":
                db.update("contacts", c["id"], {"stage": "lost", "lost_reason": reason})

    db.update("companies", company["id"], {
        "signals": json.dumps(signals, default=str), "problems": json.dumps(problems), "score": score,
        "status": status, "status_reason": reason, "site_summary": audit.summary, "enriched_at": now_iso()})
    db.log("enrich", f"{company['name']}: {status} ({reason}); signals: {', '.join(signals) or 'none'}; "
           f"contacts: {len(created)} new", company_id=company["id"])
    return db.company(company["id"])  # type: ignore[return-value]


def enrich_pending(db: DB, cfg: Config, limit: int | None = None, llm: LLM | None = None) -> int:
    n = int(limit or cfg.get("enrichment.per_cycle", 15))
    rows = db.all("SELECT * FROM companies WHERE status = 'new' ORDER BY id LIMIT ?", (n,))
    for c in rows:
        try:
            enrich_company(db, cfg, c, llm=llm)
        except Exception as exc:
            log.exception("enrich failed for %s", c["name"])
            db.update("companies", c["id"], {"status": "error", "status_reason": str(exc)[:300]})
            db.log("enrich", f"{c['name']}: error {exc}", level="error", company_id=c["id"])
    return len(rows)
