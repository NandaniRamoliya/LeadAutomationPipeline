"""The sales pipeline state machine.

Every contact is in exactly one STAGE. Most stages own a follow-up TRACK (sequence):

    new ──enroll──> cold ──reply: interested──> interested (warm track) ──booked──> meeting
     │               │                                                            │
     │               ├─ reply: not now / have someone ──> nurture (30/60/90 days) │
     │               ├─ reply: not interested ──> lost                            v
     │               ├─ unsubscribe ──> unsubscribed           proposal track <── call done
     │               ├─ bounce ──> bounced                         │
     │               └─ 5 emails, no reply ──> lost                 ├── WON ──> won (onboarding) ──> client (client_care)
     │                                                              └── silence ──> nurture ──> lost
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .config import Config
from .db import DB, iso, now_iso, parse_iso, utcnow

STAGES: dict[str, str] = {
    "new": "Found, not contacted yet",
    "cold": "Cold outreach: no reply yet",
    "interested": "Replied with interest: getting a call booked",
    "meeting": "Call booked",
    "proposal": "Call done / proposal sent: waiting for a decision",
    "nurture": "Not now: checking back later",
    "won": "Deal won: onboarding in progress",
    "client": "Active client",
    "lost": "Closed: no deal",
    "unsubscribed": "Asked not to be contacted",
    "bounced": "Email address bounced",
    "paused": "Paused (by you, or because a colleague replied)",
}

# Which follow-up track runs in which stage.
STAGE_SEQUENCE: dict[str, str] = {
    "cold": "cold",
    "interested": "warm",
    "proposal": "proposal",
    "nurture": "nurture",
    "client": "client_care",
}

TERMINAL_STAGES = {"lost", "unsubscribed", "bounced"}
ACTIVE_CONVERSATION = {"interested", "meeting", "proposal", "won", "client"}


def steps_for(cfg: Config, sequence: str | None) -> list[dict]:
    if not sequence:
        return []
    return list(cfg.get(f"sequences.{sequence}.steps", []) or [])


def step_info(cfg: Config, sequence: str | None, step: int) -> dict | None:
    steps = steps_for(cfg, sequence)
    return steps[step] if 0 <= step < len(steps) else None


def cancel_pending(db: DB, contact_id: int, kinds: tuple[str, ...] = ("sequence",), reason: str = "") -> int:
    q = ",".join("?" for _ in kinds)
    rows = db.all(
        f"SELECT id FROM messages WHERE contact_id = ? AND direction = 'out' AND status IN ('pending','approved') "
        f"AND kind IN ({q})", (contact_id, *kinds))
    for r in rows:
        db.update("messages", r["id"], {"status": "cancelled", "error": reason or "stage changed"})
    return len(rows)


def suppress(db: DB, value: str, reason: str) -> None:
    value = value.strip().lower()
    if not value:
        return
    db.execute("INSERT OR IGNORE INTO suppression (value, reason, created_at) VALUES (?, ?, ?)",
               (value, reason, now_iso()))


def is_suppressed(db: DB, email: str | None) -> bool:
    if not email:
        return False
    import hashlib
    email = email.strip().lower()
    domain = "@" + email.split("@")[-1]
    digest = "sha256:" + hashlib.sha256(email.encode()).hexdigest()
    return bool(db.scalar("SELECT 1 FROM suppression WHERE value IN (?, ?, ?)", (email, domain, digest)))


def set_stage(db: DB, cfg: Config, contact_id: int, stage: str, reason: str = "",
              start_at: datetime | None = None, first_delay_days: float | None = None) -> dict:
    """Move a contact to a new stage and (re)start that stage's follow-up track."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage '{stage}'. Valid: {', '.join(STAGES)}")
    contact = db.contact(contact_id)
    if not contact:
        raise ValueError(f"contact {contact_id} not found")

    kinds = ("sequence", "reply", "onboarding", "manual") if stage in TERMINAL_STAGES else ("sequence",)
    cancel_pending(db, contact_id, kinds, f"moved to {stage}")
    start = start_at or utcnow()
    sequence = STAGE_SEQUENCE.get(stage)
    values: dict = {"stage": stage, "sequence": sequence, "step": 0,
                    "sequence_started_at": iso(start) if sequence else None, "next_action_at": None}
    steps = steps_for(cfg, sequence)
    if sequence and steps:
        first_day = float(steps[0].get("day", 0)) if first_delay_days is None else float(first_delay_days)
        values["next_action_at"] = iso(start + timedelta(days=first_day))
    if stage == "meeting":
        # After the call, the proposal track starts automatically (unless you mark won/lost first).
        values["next_action_at"] = iso(start + timedelta(days=1))
    if stage in TERMINAL_STAGES:
        values["lost_reason"] = reason or stage
    db.update("contacts", contact_id, values)

    if stage in ("unsubscribed", "bounced") and contact.get("email"):
        suppress(db, contact["email"], reason or stage)
    db.log("stage", f"{contact.get('email') or contact.get('full_name')}: {contact['stage']} -> {stage}"
           + (f" ({reason})" if reason else ""), contact_id=contact_id, company_id=contact.get("company_id"))
    return db.contact(contact_id)  # type: ignore[return-value]


def advance_after_send(db: DB, cfg: Config, message: dict) -> None:
    """After a sequence email is sent, schedule the next step of that track."""
    contact = db.contact(message["contact_id"])
    if not contact:
        return
    now = utcnow()
    values: dict = {"last_outbound_at": iso(now)}
    if (message.get("kind") == "sequence" and message.get("sequence") == contact.get("sequence")
            and message.get("step") == contact.get("step")):
        steps = steps_for(cfg, contact["sequence"])
        nxt = contact["step"] + 1
        values["step"] = nxt
        if nxt < len(steps):
            gap = float(steps[nxt].get("day", 0)) - float(steps[nxt - 1].get("day", 0))
            values["next_action_at"] = iso(now + timedelta(days=max(gap, 1)))
        else:
            close_days = float(cfg.get(f"sequences.{contact['sequence']}.close_after_days", 7) or 0)
            values["next_action_at"] = iso(now + timedelta(days=close_days))
    db.update("contacts", contact["id"], values)


def process_timers(db: DB, cfg: Config) -> int:
    """Close finished tracks (no reply after the last step) and move booked calls to the proposal track."""
    now = now_iso()
    changed = 0
    for c in db.all("SELECT * FROM contacts WHERE next_action_at IS NOT NULL AND next_action_at <= ?", (now,)):
        if c["stage"] == "meeting":
            set_stage(db, cfg, c["id"], "proposal", "call date passed: starting proposal follow-ups")
            changed += 1
            continue
        steps = steps_for(cfg, c["sequence"])
        if c["sequence"] and c["step"] >= len(steps):
            outcome = cfg.get(f"sequences.{c['sequence']}.on_complete", "lost")
            if outcome == "none":
                db.update("contacts", c["id"], {"sequence": None, "next_action_at": None})
                db.log("sequence", f"{c['email']}: {c['sequence']} track finished", contact_id=c["id"])
            elif outcome == "nurture":
                set_stage(db, cfg, c["id"], "nurture", f"{c['sequence']} track finished without a reply")
            else:
                set_stage(db, cfg, c["id"], "lost", f"no response after {c['sequence']} track")
            changed += 1
    return changed


def enroll_new_contacts(db: DB, cfg: Config) -> int:
    """Put qualified, never-contacted email contacts into the cold track, paced per day."""
    per_day = int(cfg.get("sending.new_leads_per_day", 10))
    today_key = f"enrolled:{utcnow().date().isoformat()}"
    done_today = int(db.kv_get(today_key, 0) or 0)
    room = max(per_day - done_today, 0)
    if room == 0:
        return 0
    rows = db.all(
        """SELECT ct.* FROM contacts ct JOIN companies co ON co.id = ct.company_id
           WHERE ct.stage = 'new' AND co.status = 'qualified'
           ORDER BY co.score DESC, ct.id LIMIT ?""", (room * 3,))
    enrolled = 0
    for c in rows:
        if enrolled >= room:
            break
        if c["channel"] == "email" and (not c["email"] or is_suppressed(db, c["email"])):
            continue
        set_stage(db, cfg, c["id"], "cold", "enrolled")
        enrolled += 1
    db.kv_set(today_key, done_today + enrolled)
    return enrolled


def pause_colleagues(db: DB, contact: dict, reason: str) -> int:
    """Someone at the company replied: stop cold emails to everyone else there."""
    rows = db.all("SELECT id FROM contacts WHERE company_id = ? AND id != ? AND stage IN ('new','cold')",
                  (contact["company_id"], contact["id"]))
    for r in rows:
        _pause(db, r["id"], reason)
    return len(rows)


def _pause(db: DB, contact_id: int, reason: str) -> None:
    cancel_pending(db, contact_id, ("sequence",), reason)
    db.update("contacts", contact_id, {"stage": "paused", "sequence": None, "next_action_at": None,
                                       "lost_reason": reason})
    db.log("stage", f"paused: {reason}", contact_id=contact_id)


def delay_next_action(db: DB, contact_id: int, days: float) -> None:
    c = db.contact(contact_id)
    if not c or not c.get("next_action_at"):
        return
    base = max(parse_iso(c["next_action_at"]) or utcnow(), utcnow())
    db.update("contacts", contact_id, {"next_action_at": iso(base + timedelta(days=days))})
    # Re-time any queued drafts for this contact too.
    for m in db.all("SELECT id FROM messages WHERE contact_id=? AND status IN ('pending','approved') AND kind='sequence'",
                    (contact_id,)):
        db.update("messages", m["id"], {"scheduled_for": iso(base + timedelta(days=days))})
