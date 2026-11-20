"""Client onboarding after "yes" (from The Client Onboarding System):

    01 Contract  ->  02 Payment  ->  03 Welcome document  ->  04 Kickoff call  ->  05 Access  ->  06 Client portal

Mark a deal WON and the pipeline drafts each email at the right moment, sends polite reminders when a
step stalls, and moves the client to the client-care track once the portal is live.
You tick the steps only you can confirm (signed, paid, call done, access received, portal ready).
"""
from __future__ import annotations

import json
from datetime import timedelta

from .config import Config
from .db import DB, jloads, now_iso, parse_iso, utcnow
from .pipeline import set_stage
from .writer import Writer, queue_message

# (key, label, who ticks it)
CHECKLIST = [
    ("contract_sent", "01 Contract sent", "auto"),
    ("contract_signed", "01 Contract signed", "you"),
    ("invoice_sent", "02 Invoice / payment link sent", "auto"),
    ("payment_received", "02 Payment received", "you"),
    ("welcome_sent", "03 Welcome document sent", "auto"),
    ("kickoff_done", "04 Kickoff call done", "you"),
    ("access_requested", "05 Access list sent", "auto"),
    ("access_received", "05 All access received", "you"),
    ("portal_live", "06 Client portal live", "you"),
    ("portal_sent", "06 Portal link sent to client", "auto"),
]
MANUAL_KEYS = [k for k, _, who in CHECKLIST if who == "you"]
SENT_FLAG = {"contract": "contract_sent", "invoice": "invoice_sent", "welcome": "welcome_sent",
             "access_request": "access_requested", "portal_live": "portal_sent"}


def start(db: DB, cfg: Config, contact_id: int, value: float | None = None, currency: str | None = None,
          notes: str | None = None) -> dict:
    contact = db.contact(contact_id)
    if not contact:
        raise ValueError("contact not found")
    deal = db.one("SELECT * FROM deals WHERE contact_id = ?", (contact_id,))
    if not deal:
        db.insert("deals", {"contact_id": contact_id, "company_id": contact["company_id"], "status": "onboarding",
                            "value": value, "currency": currency, "checklist": "{}", "reminders": "{}",
                            "won_at": now_iso(), "notes": notes})
    else:
        db.update("deals", deal["id"], {"status": "onboarding", "value": value or deal["value"]})
    set_stage(db, cfg, contact_id, "won", "deal won")
    # Other contacts at the company stop getting sales emails.
    for other in db.all("SELECT id FROM contacts WHERE company_id=? AND id != ? AND stage NOT IN ('won','client','unsubscribed','bounced')",
                        (contact["company_id"], contact_id)):
        set_stage(db, cfg, other["id"], "paused", "company became a client")
    tick(db, cfg)
    return db.one("SELECT * FROM deals WHERE contact_id = ?", (contact_id,))  # type: ignore[return-value]


def set_step(db: DB, cfg: Config, contact_id: int, key: str, done: bool = True) -> dict:
    deal = db.one("SELECT * FROM deals WHERE contact_id = ?", (contact_id,))
    if not deal:
        raise ValueError("no deal for this contact. Mark it as WON first.")
    if key not in {k for k, _, _ in CHECKLIST}:
        raise ValueError(f"unknown step '{key}'. Valid: {', '.join(k for k, _, _ in CHECKLIST)}")
    cl = jloads(deal["checklist"], {})
    if done:
        cl[key] = now_iso()
    else:
        cl.pop(key, None)
    db.update("deals", deal["id"], {"checklist": json.dumps(cl)})
    db.log("onboarding", f"step {key} {'done' if done else 'undone'}", contact_id=contact_id)
    tick(db, cfg)
    return db.one("SELECT * FROM deals WHERE id = ?", (deal["id"],))  # type: ignore[return-value]


def _last_message(db: DB, contact_id: int, purposes: tuple[str, ...]) -> dict | None:
    q = ",".join("?" for _ in purposes)
    return db.one(f"""SELECT * FROM messages WHERE contact_id=? AND kind='onboarding' AND purpose IN ({q})
                      AND status IN ('pending','approved','sent') ORDER BY id DESC LIMIT 1""", (contact_id, *purposes))


def tick(db: DB, cfg: Config, writer: Writer | None = None) -> int:
    """Advance every open onboarding. Returns number of emails drafted."""
    writer = writer or Writer(db, cfg)
    o = cfg.section("onboarding")
    every = float(o.get("reminder_every_days", 2))
    max_rem = int(o.get("max_reminders", 3))
    auto = bool(db.kv_get("autopilot_replies", cfg.get("approval.autopilot_replies", False)))
    drafted = 0
    for deal in db.all("SELECT * FROM deals WHERE status = 'onboarding'"):
        contact = db.contact(deal["contact_id"])
        if not contact:
            continue
        company = db.company(contact["company_id"]) or {}
        cl = jloads(deal["checklist"], {})
        rem = jloads(deal["reminders"], {})

        # Auto-tick "*_sent" steps once the email actually went out.
        for purpose, flag in SENT_FLAG.items():
            if flag not in cl and db.scalar("""SELECT 1 FROM messages WHERE contact_id=? AND kind='onboarding'
                                               AND purpose=? AND status='sent'""", (contact["id"], purpose)):
                cl[flag] = now_iso()

        def queue(key: str) -> None:
            nonlocal drafted
            draft = writer.onboarding_draft(contact, company, key)
            queue_message(db, cfg, contact, company, draft, "onboarding", purpose=key, auto_ok=auto)
            drafted += 1
            db.log("onboarding", f"drafted '{key}' for {company.get('name')}", contact_id=contact["id"])

        def step(main: str, reminder: str, done_key: str) -> bool:
            """Ensure `main` email exists; remind until `done_key` is ticked. Returns True if still waiting."""
            if done_key in cl:
                return False
            last = _last_message(db, contact["id"], (main, reminder))
            if not last:
                queue(main)
                return True
            if last["status"] == "sent" and rem.get(main, 0) < max_rem:
                sent_at = parse_iso(last["sent_at"])
                if sent_at and utcnow() - sent_at >= timedelta(days=every):
                    queue(reminder)
                    rem[main] = rem.get(main, 0) + 1
            return True

        if step("contract", "contract_reminder", "contract_signed"):
            pass
        elif step("invoice", "payment_reminder", "payment_received"):
            pass
        elif step("welcome", "kickoff_reminder", "kickoff_done"):
            pass
        elif step("access_request", "access_reminder", "access_received"):
            pass
        elif "portal_live" in cl:
            if not _last_message(db, contact["id"], ("portal_live",)):
                queue("portal_live")
            elif "portal_sent" in cl:
                deal_done(db, cfg, deal, contact)
        db.update("deals", deal["id"], {"checklist": json.dumps(cl), "reminders": json.dumps(rem)})
    return drafted


def deal_done(db: DB, cfg: Config, deal: dict, contact: dict) -> None:
    db.update("deals", deal["id"], {"status": "active", "completed_at": now_iso()})
    set_stage(db, cfg, contact["id"], "client", "onboarding complete: ready to execute")
    db.log("onboarding", "onboarding complete, client-care track started", contact_id=contact["id"])


def progress(deal: dict) -> tuple[int, int]:
    cl = jloads(deal.get("checklist"), {})
    return sum(1 for k, _, _ in CHECKLIST if k in cl), len(CHECKLIST)


__all__ = ["start", "set_step", "tick", "CHECKLIST", "MANUAL_KEYS", "progress"]
