"""Your To-do list: everything that needs a human, in one place, most valuable first.

  replies   people who answered: check the drafted answer and send
  decide    "let's go ahead", proposal requests, unclear replies
  messages  WhatsApp / Instagram / LinkedIn / X / Facebook messages to send (one click opens the chat)
  calls     phone calls with a script; you log what happened
  emails    outreach emails waiting for your OK (hidden when autopilot is on)
  clients   onboarding steps only you can confirm (signed? paid? kickoff done?)
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from . import channels as chn
from .config import Config
from .db import DB, iso, jloads, now_iso, utcnow
from .pipeline import advance_after_send

SECTIONS = [
    ("replies", "Replies & client messages", "People who answered, and messages to your clients. Check the draft, edit if you like, send."),
    ("decide", "Your decision needed", "Someone wants to go ahead, asked for a proposal, or wrote something unclear."),
    ("calls", "Calls to make", "Tap the number to dial, use the script, then press what happened."),
    ("messages", "Messages to send", "One click opens the chat with the message ready (WhatsApp) or copies it. Send it, then press Done."),
    ("emails", "Emails waiting for your OK", "Outreach emails. Approve them and they go out automatically at the right time."),
    ("clients", "Client steps", "Tick these when they happen; the next onboarding email is written for you."),
]

CALL_OUTCOMES = {
    "no_answer": "No answer",
    "voicemail": "Left voicemail",
    "interested": "Talked: interested",
    "booked": "Talked: call/meeting booked",
    "call_back": "Call back later",
    "not_interested": "Not interested",
    "wrong_number": "Wrong number",
}


def todo(db: DB, cfg: Config, horizon_hours: float = 12) -> dict[str, list[dict]]:
    horizon = iso(utcnow() + timedelta(hours=horizon_hours))
    out: dict[str, list[dict]] = {k: [] for k, _, _ in SECTIONS}
    rows = db.all("""SELECT m.*, co.name company, co.website, co.phone company_phone, co.score, co.priority, co.city,
                            ct.full_name, ct.first_name, ct.email, ct.stage, ct.call_attempts
                     FROM messages m LEFT JOIN companies co ON co.id = m.company_id LEFT JOIN contacts ct ON ct.id = m.contact_id
                     WHERE m.direction='out' AND m.status IN ('pending','approved')
                     ORDER BY co.priority DESC, co.score DESC, m.scheduled_for""")
    for m in rows:
        m["issues"] = jloads(m.get("check_issues"), [])
        m["blocked"] = any(i["level"] == "block" for i in m["issues"])
        m["channel_label"] = chn.LABELS.get(m["channel"], m["channel"])
        m["icon"] = chn.ICONS.get(m["channel"], "•")
        if m["kind"] == "reply":
            if m["status"] == "pending":
                m["their_message"] = _last_inbound(db, m["contact_id"])
                (out["decide"] if m["needs_human"] else out["replies"]).append(m)
            elif m["channel"] not in ("email",):
                out["messages"].append(m)
            continue
        if m["kind"] == "onboarding":
            if m["status"] == "pending":
                out["replies"].append(m)
            continue
        if m["channel"] == "email":
            if m["status"] == "pending":
                out["emails"].append(m)
            continue
        if (m["scheduled_for"] or "") > horizon:
            continue
        (out["calls"] if m["channel"] == "call" else out["messages"]).append(m)
    # Onboarding steps waiting on you
    from .onboarding import CHECKLIST
    for d in db.all("""SELECT d.*, co.name company, ct.full_name, ct.id cid FROM deals d JOIN contacts ct ON ct.id = d.contact_id
                       LEFT JOIN companies co ON co.id = d.company_id WHERE d.status = 'onboarding'"""):
        cl = jloads(d["checklist"], {})
        nxt = next(((k, label, who) for k, label, who in CHECKLIST if k not in cl), None)
        if nxt and nxt[2] == "you":
            out["clients"].append({"deal": d, "key": nxt[0], "label": nxt[1], "company": d["company"], "contact_id": d["cid"]})
    return out


def counts(db: DB, cfg: Config) -> dict[str, int]:
    t = todo(db, cfg)
    c = {k: len(v) for k, v in t.items()}
    c["total"] = sum(c.values())
    return c


def _last_inbound(db: DB, contact_id: int) -> dict | None:
    return db.one("SELECT body, intent, received_at, channel FROM messages WHERE contact_id=? AND direction='in' "
                  "ORDER BY id DESC LIMIT 1", (contact_id,))


# ---------------------------------------------------------------------------
# Completing tasks
# ---------------------------------------------------------------------------
def done(db: DB, cfg: Config, message_id: int, body: str | None = None) -> None:
    """You sent the DM (or reply on a social channel): record it and schedule the next step."""
    m = db.message(message_id)
    if not m:
        return
    vals = {"status": "sent", "sent_at": now_iso()}
    if body is not None:
        vals["body"] = body
    db.update("messages", message_id, vals)
    if m["kind"] == "sequence":
        advance_after_send(db, cfg, m)
    else:
        db.update("contacts", m["contact_id"], {"last_outbound_at": now_iso()})
    db.log("task", f"{chn.LABELS.get(m['channel'], m['channel'])} message sent by you", contact_id=m["contact_id"])


def skip(db: DB, cfg: Config, message_id: int) -> None:
    m = db.message(message_id)
    if not m:
        return
    db.update("messages", message_id, {"status": "cancelled", "error": "skipped by you"})
    if m["kind"] == "sequence":
        advance_after_send(db, cfg, m)
    db.log("task", "task skipped", contact_id=m["contact_id"])


def call_outcome(db: DB, cfg: Config, message_id: int, outcome: str, notes: str = "", writer=None) -> str:
    """Record what happened on a call and move the lead accordingly. Returns a short message for you."""
    m = db.message(message_id)
    if not m:
        return "Task not found."
    contact = db.contact(m["contact_id"])
    company = db.company(m["company_id"]) or {}
    attempts = int(contact.get("call_attempts") or 0) + 1
    db.update("contacts", contact["id"], {"call_attempts": attempts})
    label = CALL_OUTCOMES.get(outcome, outcome)
    db.log("call", f"call to {company.get('name')}: {label}" + (f" ({notes})" if notes else ""), contact_id=contact["id"])

    if outcome == "no_answer" and attempts < int(cfg.get("channels.call_attempts", 2)):
        db.update("messages", message_id, {"scheduled_for": iso(utcnow() + timedelta(days=1)), "outcome": "no_answer"})
        return "No answer. I'll put this call back on your list tomorrow."
    db.update("messages", message_id, {"status": "sent", "sent_at": now_iso(), "outcome": outcome,
                                       "error": notes or None})
    if outcome in ("no_answer", "voicemail"):
        if m["kind"] == "sequence":
            advance_after_send(db, cfg, m)
        return "Logged. The next follow-up is scheduled."
    if outcome == "wrong_number":
        db.update("companies", company["id"], {"phone": None})
        if m["kind"] == "sequence":
            advance_after_send(db, cfg, m)
        return "Number removed. The next follow-up is scheduled."
    # They picked up: treat it like a reply.
    from .classifier import Classification
    from .handler import apply_intent
    from .inbox import InboundMail
    from .writer import Writer
    intent = {"interested": "interested", "booked": "meeting_booked", "call_back": "not_now",
              "not_interested": "not_interested"}.get(outcome, "other")
    days = 30 if outcome == "call_back" else None
    mail = InboundMail(message_id=f"<call-{secrets.token_hex(6)}@local>", from_addr=contact.get("email") or "",
                       from_name=contact.get("full_name") or "", subject="Phone call",
                       body=notes or f"(phone call) {label}")
    apply_intent(db, cfg, contact, mail, Classification(intent, 1.0, "you", followup_in_days=days),
                 writer or Writer(db, cfg), channel_hint="call")
    return {"interested": "Great! They're now 'interested': a follow-up message is drafted.",
            "booked": "Call booked! Follow-ups after the meeting are scheduled.",
            "call_back": "Noted. They'll be contacted again in about a month.",
            "not_interested": "Closed. They won't be contacted again."}.get(outcome, "Logged.")


def log_reply(db: DB, cfg: Config, contact_id: int, text: str, intent: str | None = None, channel: str = "whatsapp",
              writer=None) -> dict:
    """They answered on WhatsApp / Instagram / LinkedIn / phone: handle it exactly like an email reply."""
    from .classifier import Classification, classify
    from .handler import apply_intent
    from .inbox import InboundMail
    from .writer import Writer
    contact = db.contact(contact_id)
    result = Classification(intent, 1.0, "you") if intent else classify(cfg, text)
    mail = InboundMail(message_id=f"<{channel}-{secrets.token_hex(6)}@local>", from_addr=contact.get("email") or "",
                       from_name=contact.get("full_name") or "", subject=f"Reply on {chn.LABELS.get(channel, channel)}",
                       body=text)
    return apply_intent(db, cfg, contact, mail, result, writer or Writer(db, cfg), channel_hint=channel)


def export_call_list(db: DB, cfg: Config) -> list[dict]:
    """Calls due, as rows for a spreadsheet (for you or a telecaller)."""
    rows = []
    for m in todo(db, cfg, horizon_hours=24)["calls"]:
        rows.append({"company": m["company"], "phone": m["to_addr"], "city": m.get("city"), "score": m.get("score"),
                     "script": m["body"], "task_id": m["id"]})
    return rows


__all__ = ["todo", "counts", "done", "skip", "call_outcome", "log_reply", "SECTIONS", "CALL_OUTCOMES"]
