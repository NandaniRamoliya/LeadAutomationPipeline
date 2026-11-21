"""Research job: audit each new company, find the right people + emails + other channels, verify, score, qualify."""
from __future__ import annotations

import json
import logging

from ..channels import available, parse_phone
from ..config import Config
from ..db import DB, now_iso
from ..llm import LLM
from ..pipeline import is_suppressed
from ..templates import SIGNALS
from .patterns import apply_pattern, learn_pattern
from .verify import is_sendable, verify_email
from .website import SiteAudit, audit_website

log = logging.getLogger("leadpipe.enrich")

FREE_MAIL = {"gmail.com", "yahoo.com", "yahoo.co.in", "hotmail.com", "outlook.com", "live.com", "icloud.com",
             "rediffmail.com", "aol.com", "proton.me", "protonmail.com", "zoho.com", "gmx.com", "ymail.com"}
OWNER_WORDS = ("founder", "owner", "ceo", "proprietor", "principal", "managing", "director", "partner", "president", "doctor")

AUDIT_SYSTEM = ("You are a careful website auditor working for a freelancer. From the verified data only, write the 2 "
                "most important, specific problems this business has that the freelancer's service fixes, in plain "
                "words a business owner understands (max 14 words each). Do not invent anything; if unsure, say less.")
AUDIT_SCHEMA = {"type": "object", "properties": {"problems": {"type": "array", "items": {"type": "string"}}},
                "required": ["problems"], "additionalProperties": False}


def score_company(cfg: Config, signals: dict, has_named_person: bool, best_status: str | None, priority: bool = False) -> float:
    weights = cfg.section("signals")
    score = sum(float(weights.get(k, 0) or 0) for k in signals)
    if has_named_person:
        score += 10
    if best_status == "valid":
        score += 5
    if priority:
        score += float(cfg.get("enrichment.priority_bonus", 30))
    return round(min(score, 100.0), 1)


def _rank_emails(emails: list[str], domain: str | None, people: list[dict]) -> list[tuple[str, dict | None]]:
    """Personal same-domain emails matched to a person first, then other same-domain, then free-mail."""
    from .patterns import name_parts
    ranked: list[tuple[int, str, dict | None]] = []
    for e in emails:
        local, _, edom = e.partition("@")
        if domain and edom != domain and not edom.endswith("." + domain) and edom not in FREE_MAIL:
            continue  # someone else's address (web agency, plugin vendor...)
        person = None
        for p in people:
            parts = name_parts(p["full_name"])
            first = parts[0] if parts else p["full_name"].split()[0].lower()
            if len(first) > 2 and first in local:
                person = p
                break
        rank = 0 if person else (3 if local in ("info", "contact", "hello", "office", "enquiry", "enquiries") else 2)
        if edom in FREE_MAIL:
            rank += 1
        ranked.append((rank, e, person))
    ranked.sort(key=lambda r: r[0])
    return [(e, p) for _, e, p in ranked]


def _first_name(full: str | None) -> str | None:
    if not full:
        return None
    first = full.replace("Dr. ", "Dr ").split()[0]
    return ("Dr " + full.split()[-1]) if first == "Dr" else first


def reputation_signals(company: dict) -> dict:
    sig = {}
    r, n = company.get("rating"), company.get("reviews")
    if r is not None and float(r) > 0 and float(r) < 4.0:
        sig["low_rating"] = {"rating": f"{float(r):.1f}"}
    if n is not None and int(n) < 15:
        sig["few_reviews"] = {"reviews": str(int(n))}
    return sig


def enrich_company(db: DB, cfg: Config, company: dict, llm: LLM | None = None,
                   audit: SiteAudit | None = None) -> dict:
    ecfg = cfg.section("enrichment")
    if audit is None:
        site = company.get("website")
        if not site and company.get("instagram"):
            site = company["instagram"]
        audit = audit_website(site, timeout=float(ecfg.get("request_timeout", 12)),
                              max_pages=int(ecfg.get("max_pages_per_site", 6)))
    signals = dict(audit.signals)
    signals.update(reputation_signals(company))
    roles_wanted = [r.lower() for r in (ecfg.get("hiring_roles") or []) if r]
    if roles_wanted and isinstance(signals.get("hiring"), dict):
        hits = [r for r in str(signals["hiring"].get("roles", "")).split(", ") if any(w in r.lower() for w in roles_wanted)]
        if hits:
            signals["hiring"]["roles"] = ", ".join(hits)
        else:
            signals.pop("hiring")
    domain = company.get("domain")

    # Channels found on the site: fill in what the listing didn't have.
    extra: dict = {}
    for ch in ("instagram", "facebook", "linkedin", "twitter", "whatsapp"):
        if audit.socials.get(ch) and not company.get(ch):
            extra[ch] = audit.socials[ch]
    if not company.get("phone") and audit.phones:
        ph = parse_phone(audit.phones[0], company.get("country"))
        if ph:
            extra["phone"] = ph["e164"]
    if audit.tech:
        extra["tech"] = audit.tech
    if extra:
        db.update("companies", company["id"], extra)
        company = {**company, **extra}

    candidates = list(audit.emails)
    if company.get("listed_email") and company["listed_email"] not in candidates:
        candidates.insert(0, company["listed_email"])
    ranked = _rank_emails(candidates, domain, audit.people)

    max_contacts = int(ecfg.get("max_contacts_per_company", 2))
    existing = db.all("SELECT * FROM contacts WHERE company_id = ?", (company["id"],))
    slots = max_contacts - len([c for c in existing if c.get("email")])
    created: list[int] = []
    best_status = None

    def add_contact(email: str, status: str, reason: str, source: str, person: dict | None) -> None:
        full = person["full_name"] if person else None
        created.append(db.insert("contacts", {
            "company_id": company["id"], "full_name": full, "first_name": _first_name(full),
            "role": person["role"] if person else None, "email": email, "email_status": status,
            "email_source": source, "channel": "email", "stage": "new", "notes": reason}))

    # 1. The owner/decision-maker's own address: published, or built from the company's email pattern.
    owners = sorted(audit.people, key=lambda p: 0 if any(w in p.get("role", "").lower() for w in OWNER_WORDS) else 1)
    pattern = learn_pattern(candidates, audit.people, domain) if domain else None
    for person, (email, matched) in [(p, next(((e, m) for e, m in ranked if m is p), (None, None))) for p in owners[:2]]:
        if slots - len(created) <= 0:
            break
        if not email and pattern and domain:
            email = apply_pattern(pattern, person, audit.people, domain)
            if email and not is_suppressed(db, email) and not db.scalar("SELECT 1 FROM contacts WHERE email=?", (email,)):
                v = verify_email(email, cfg, pattern=True)
                if v.status != "invalid":
                    best_status = best_status or v.status
                    add_contact(email, v.status, f"{v.reason} ({pattern})", "pattern", person)
            continue
        if email and not is_suppressed(db, email) and not db.scalar("SELECT 1 FROM contacts WHERE email=?", (email,)):
            v = verify_email(email, cfg)
            if v.status != "invalid":
                best_status = best_status or v.status
                add_contact(email, v.status, v.reason, "website", person)

    # 2. Other published addresses (personal first, then info@/contact@).
    for email, person in ranked:
        if len(created) >= slots:
            break
        if is_suppressed(db, email) or db.scalar("SELECT 1 FROM contacts WHERE email = ?", (email,)):
            continue
        v = verify_email(email, cfg)
        if v.status == "invalid":
            continue
        best_status = best_status or v.status
        if not person and v.status == "risky" and not created and audit.people:
            person = owners[0] if owners else None  # greet the owner by name on the general inbox
        add_contact(email, v.status, v.reason, "website" if email in audit.emails else "listing", person)

    # 3. Blind guess (only if allowed) when nothing else was found.
    if not created and not existing and audit.people and domain and ecfg.get("allow_guessed_emails"):
        from .patterns import name_parts
        parts = name_parts(owners[0]["full_name"])
        if parts:
            guess = f"{parts[0]}@{domain}"
            v = verify_email(guess, cfg, guessed=True)
            if v.status != "invalid" and not db.scalar("SELECT 1 FROM contacts WHERE email = ?", (guess,)):
                best_status = v.status
                add_contact(guess, v.status, v.reason, "guess", owners[0])

    existing = db.all("SELECT * FROM contacts WHERE company_id = ?", (company["id"],))
    sendable = [c for c in existing if c.get("email") and is_sendable(c["email_status"], cfg)]
    if not best_status and sendable:
        best_status = sendable[0]["email_status"]
    has_person = any(c.get("full_name") for c in existing) or bool(audit.people)
    score = score_company(cfg, signals, has_person, best_status, bool(company.get("priority")))

    # No usable email: one "social" contact reached by WhatsApp / Instagram / LinkedIn / call tasks.
    chans = available(company, None, cfg)
    if not sendable and chans and not any(c["channel"] in ("social", "manual") for c in existing):
        owner = owners[0] if owners else None
        db.insert("contacts", {"company_id": company["id"], "channel": "social", "stage": "new", "email_status": "none",
                               "full_name": owner["full_name"] if owner else None,
                               "first_name": _first_name(owner["full_name"]) if owner else None,
                               "role": owner["role"] if owner else None,
                               "notes": "no email: reach by " + ", ".join(k for k in chans)})
        existing = db.all("SELECT * FROM contacts WHERE company_id = ?", (company["id"],))

    from ..templates import fill
    problems = [fill(SIGNALS[k]["problem"], signals[k] if isinstance(signals[k], dict) else {})
                for k in sorted(signals, key=lambda k: -float(cfg.get(f"signals.{k}", 0) or 0))
                if k in SIGNALS and float(cfg.get(f"signals.{k}", 0) or 0) > 0][:3]
    if llm and llm.available and score >= float(ecfg.get("min_score", 25)) and audit.ok:
        out = llm.json(AUDIT_SYSTEM, json.dumps({
            "freelancer_service": cfg.get("profile.service_long"), "business": company.get("name"),
            "category": company.get("category"), "verified_signals": signals, "site_text": audit.summary[:1200]},
            ensure_ascii=False, default=str), AUDIT_SCHEMA, effort=str(cfg.get("llm.effort_classify", "low")))
        if out and out.get("problems"):
            problems = [str(p)[:140] for p in out["problems"][:2]] + problems[:1]

    restricted = {c.upper() for c in (cfg.get("compliance.restricted_countries") or [])}
    reachable = bool(sendable) or any(c["channel"] in ("social", "manual") for c in existing)
    if (company.get("country") or "").upper() in restricted:
        status, reason = "disqualified", f"country {company.get('country')} is restricted in compliance settings"
    elif not reachable:
        status, reason = "disqualified", "no email, phone or social profile found"
    elif score < float(ecfg.get("min_score", 25)) and not company.get("priority"):
        status, reason = "disqualified", f"score {score} below min_score (few problems your service fixes)"
    else:
        status, reason = "qualified", f"score {score}" + (" (high-value)" if company.get("priority") else "")
    if status == "disqualified":
        for c in existing:
            if c["stage"] == "new":
                db.update("contacts", c["id"], {"stage": "lost", "lost_reason": reason})

    db.update("companies", company["id"], {
        "signals": json.dumps(signals, default=str), "problems": json.dumps(problems), "score": score,
        "status": status, "status_reason": reason, "site_summary": audit.summary, "enriched_at": now_iso()})
    db.log("enrich", f"{company['name']}: {status} ({reason}); found: {', '.join(signals) or 'nothing'}; "
           f"channels: {', '.join(available(company, None, cfg)) or 'email only'}; {len(created)} new contacts",
           company_id=company["id"])
    return db.company(company["id"])  # type: ignore[return-value]


def enrich_pending(db: DB, cfg: Config, limit: int | None = None, llm: LLM | None = None) -> int:
    n = int(limit or cfg.get("enrichment.per_cycle", 15))
    rows = db.all("SELECT * FROM companies WHERE status = 'new' ORDER BY priority DESC, id LIMIT ?", (n,))
    for c in rows:
        try:
            enrich_company(db, cfg, c, llm=llm)
        except Exception as exc:
            log.exception("enrich failed for %s", c["name"])
            db.update("companies", c["id"], {"status": "error", "status_reason": str(exc)[:300]})
            db.log("enrich", f"{c['name']}: error {exc}", level="error", company_id=c["id"])
    return len(rows)


def _allowed_osm_values(cfg: Config) -> set[str]:
    from ..discovery.osm import tags_for
    vals: set[str] = set()
    for t in cfg.get("reach.business_types") or []:
        for tag in tags_for(t):
            for part in tag.split("]["):
                vals.add(part.split("=")[-1].lower())
    return vals


def rescore_all(db: DB, cfg: Config) -> dict:
    """Re-score every researched company with the current settings (after you change your service type, signal
    weights or business types). Leads that now qualify go back in the queue. Leads that no longer fit and haven't
    been contacted yet are set aside, and their waiting messages are cancelled."""
    from ..pipeline import set_stage
    min_score = float(cfg.get("enrichment.min_score", 25))
    allowed = _allowed_osm_values(cfg)
    out = {"qualified": 0, "set_aside": 0}
    for co in db.all("SELECT * FROM companies WHERE status IN ('qualified','disqualified') AND enriched_at IS NOT NULL"):
        if (co.get("status_reason") or "").startswith(("country", "no email, phone")):
            continue
        contacts = db.all("SELECT * FROM contacts WHERE company_id = ?", (co["id"],))
        best = next((c["email_status"] for c in contacts if c.get("email")), None)
        score = score_company(cfg, json.loads(co.get("signals") or "{}"), any(c.get("full_name") for c in contacts),
                              best, bool(co.get("priority")))
        wrong_type = (co.get("source") == "osm" and allowed and (co.get("category") or "").lower() not in allowed
                      and not co.get("priority"))
        ok = (score >= min_score or bool(co.get("priority"))) and not wrong_type
        vals: dict = {"score": score}
        if ok and co["status"] == "disqualified":
            vals.update(status="qualified", status_reason=f"score {score}")
            for c in contacts:
                if c["stage"] == "lost" and (c.get("lost_reason") or "").startswith(("score", "business type")):
                    db.update("contacts", c["id"], {"stage": "new", "lost_reason": None})
            out["qualified"] += 1
        elif not ok and co["status"] == "qualified":
            touched = db.scalar("""SELECT 1 FROM messages WHERE company_id = ? AND (status = 'sent' OR direction = 'in')""",
                                (co["id"],))
            if not touched:
                reason = ("business type no longer in your list" if wrong_type
                          else f"score {score} below min_score (few problems your service fixes)")
                vals.update(status="disqualified", status_reason=reason)
                for c in contacts:
                    if c["stage"] in ("new", "cold"):
                        set_stage(db, cfg, c["id"], "lost", reason)
                out["set_aside"] += 1
        db.update("companies", co["id"], vals)
    db.log("enrich", f"re-checked leads with your new settings: {out['qualified']} now qualify, {out['set_aside']} set aside")
    return out
