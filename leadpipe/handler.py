"""The Handler / Closer: matches each incoming email to a lead, understands it, moves the lead to the right
stage (which starts the right follow-up track), drafts the answer, and alerts you when it's hot."""
from __future__ import annotations

import json
import logging

from .classifier import HOT, INTENTS, NEEDS_HUMAN, Classification, classify
from .config import Config
from .db import DB, now_iso
from .discovery import normalize_domain
from .enrich import FREE_MAIL
from .enrich.verify import verify_email
from .inbox import InboundMail, fetch_new, mark_seen
from .llm import LLM
from .notify import notify
from .pipeline import ACTIVE_CONVERSATION, cancel_pending, delay_next_action, pause_colleagues, set_stage, suppress
from .writer import Writer, queue_message

log = logging.getLogger("leadpipe.handler")

# Replies that may go out without you when autopilot_replies is on. Everything else always waits for you.
SAFE_AUTO_REPLY = {"interested", "meeting_booked", "timeline_question", "portfolio_request", "price_question"}


def match_contact(db: DB, cfg: Config, mail: InboundMail) -> dict | None:
    ids = [mail.in_reply_to, *mail.references]
    ids = [i for i in ids if i]
    if ids:
        q = ",".join("?" for _ in ids)
        row = db.one(f"SELECT contact_id FROM messages WHERE direction='out' AND message_id IN ({q}) ORDER BY id DESC LIMIT 1", ids)
        if row:
            return db.contact(row["contact_id"])
    for rcpt in mail.dsn_recipients:
        c = db.one("SELECT * FROM contacts WHERE email = ?", (rcpt,))
        if c:
            return c
    c = db.one("SELECT * FROM contacts WHERE email = ?", (mail.from_addr,))
    if c:
        return c
    # A colleague at a company we emailed (same, non-free domain) -> new contact under that company.
    domain = mail.from_addr.split("@")[-1]
    from .mailboxes import mailboxes
    own_domains = {m.domain for m in mailboxes(cfg)} | {cfg.sending_domain}
    if domain and domain not in FREE_MAIL and domain not in own_domains:
        company = db.one("""SELECT co.* FROM companies co JOIN contacts ct ON ct.company_id = co.id
                            WHERE co.domain = ? AND ct.last_outbound_at IS NOT NULL LIMIT 1""", (normalize_domain(domain),))
        if company:
            first = (mail.from_name or "").split()[0] if mail.from_name else None
            cid = db.insert("contacts", {"company_id": company["id"], "full_name": mail.from_name or None,
                                         "first_name": first, "email": mail.from_addr, "email_status": "valid",
                                         "email_source": "replied", "channel": "email", "stage": "new",
                                         "notes": "replied on behalf of a colleague"})
            return db.contact(cid)
    return None


def handle_mail(db: DB, cfg: Config, mail: InboundMail, writer: Writer | None = None, llm: LLM | None = None) -> dict | None:
    from .mailboxes import own_addresses
    if mail.from_addr in own_addresses(cfg) or mail.from_addr == (cfg.from_email or "").lower():
        mark_seen(db, mail.message_id)
        return None
    contact = match_contact(db, cfg, mail)
    if not contact:
        mark_seen(db, mail.message_id)
        return None
    llm = llm or LLM(cfg)
    writer = writer or Writer(db, cfg, llm)
    result = classify(cfg, mail.body, mail.subject, mail.headers, mail.from_addr, llm)
    return apply_intent(db, cfg, contact, mail, result, writer)


def _suppress_contact(db: DB, cfg: Config, contact: dict, company: dict, reason: str) -> None:
    """Do-not-contact on every identifier we have for them (email, and phone/handles for DM/call leads)."""
    from . import channels as chn
    if contact.get("email"):
        suppress(db, contact["email"], reason)
    else:
        for v in chn.available(company, contact, None).values():
            suppress(db, v, reason)


def apply_intent(db: DB, cfg: Config, contact: dict, mail: InboundMail, result: Classification,
                 writer: Writer, channel_hint: str | None = None) -> dict:
    company = db.company(contact["company_id"]) or {}
    intent = result.intent
    needs_human = intent in NEEDS_HUMAN
    msg_id = db.insert("messages", {
        "contact_id": contact["id"], "company_id": contact["company_id"], "direction": "in",
        "kind": "inbound", "subject": mail.subject, "body": mail.body[:20000], "status": "received",
        "channel": channel_hint or "email",
        "message_id": mail.message_id, "in_reply_to": mail.in_reply_to, "from_addr": mail.from_addr,
        "intent": intent, "intent_detail": json.dumps({"confidence": result.confidence, "source": result.source,
                                                        "summary": result.summary, "followup_in_days": result.followup_in_days,
                                                        "referral_email": result.referral_email}),
        "needs_human": 1 if needs_human else 0, "received_at": now_iso(),
    })
    mark_seen(db, mail.message_id)
    label = contact.get("email") or contact.get("full_name")
    db.log("reply", f"{label}: {INTENTS.get(intent, intent)} ({result.source}, {result.confidence:.0%})",
           contact_id=contact["id"], company_id=contact["company_id"])

    # ---- delivery problems / auto replies: no human involved ----------------
    if intent == "bounce":
        set_stage(db, cfg, contact["id"], "bounced", "email bounced")
        db.update("contacts", contact["id"], {"email_status": "invalid"})
        db.update("messages", msg_id, {"handled": 1})
        return {"intent": intent, "contact_id": contact["id"]}
    if intent == "auto_reply":
        delay_next_action(db, contact["id"], float(cfg.get("replies.ooo_delay_days", 4)))
        db.update("messages", msg_id, {"handled": 1})
        return {"intent": intent, "contact_id": contact["id"]}

    # ---- a real human replied ---------------------------------------------------
    db.update("contacts", contact["id"], {"last_inbound_at": now_iso()})
    cancel_pending(db, contact["id"], ("sequence",), "they replied")
    if cfg.get("replies.stop_company_on_reply", True):
        pause_colleagues(db, contact, f"{label} at the same company replied")

    stage = contact["stage"]
    is_client = stage in ("won", "client")
    autopilot = bool(db.kv_get("autopilot_replies", cfg.get("approval.autopilot_replies", False)))

    if intent == "unsubscribe":
        _suppress_contact(db, cfg, contact, company, "asked to unsubscribe")
        if is_client:
            db.update("contacts", contact["id"], {"sequence": None, "next_action_at": None})
        else:
            set_stage(db, cfg, contact["id"], "unsubscribed", "asked to stop")
        db.update("messages", msg_id, {"handled": 1})
        return {"intent": intent, "contact_id": contact["id"]}

    if is_client:
        # Clients talk to you directly; the pipeline only flags it.
        db.update("messages", msg_id, {"needs_human": 1})
        notify(db, cfg, f"Client {company.get('name')} replied", f"{label}: {mail.subject}\n\n{mail.body[:600]}")
        return {"intent": intent, "contact_id": contact["id"], "needs_human": True}

    # Stage transition = which follow-up track runs next
    if intent == "not_interested":
        set_stage(db, cfg, contact["id"], "lost", "not interested")
        _suppress_contact(db, cfg, contact, company, "said not interested")
    elif intent == "not_now":
        set_stage(db, cfg, contact["id"], "nurture", "not now", first_delay_days=result.followup_in_days or 60)
    elif intent == "already_have":
        set_stage(db, cfg, contact["id"], "nurture", "already has someone", first_delay_days=90)
    elif intent == "meeting_booked":
        set_stage(db, cfg, contact["id"], "meeting", "call booked")
    elif intent in ("ready_to_buy", "proposal_request"):
        set_stage(db, cfg, contact["id"], "proposal", INTENTS[intent])
        if intent == "ready_to_buy":
            # Stop proposal chasers: they said yes. You confirm with "Mark as WON" to start onboarding.
            db.update("contacts", contact["id"], {"sequence": None, "next_action_at": None})
    elif intent in ("interested", "price_question", "portfolio_request", "timeline_question", "question", "referral"):
        if stage not in ACTIVE_CONVERSATION or stage == "interested":
            set_stage(db, cfg, contact["id"], "interested", INTENTS[intent])
    else:  # other / unclear
        if stage in ("cold", "nurture", "paused", "new"):
            set_stage(db, cfg, contact["id"], "interested", "replied (unclear): needs you")

    if intent == "referral" and result.referral_email:
        ref = result.referral_email.lower()
        if not db.scalar("SELECT 1 FROM contacts WHERE email=?", (ref,)) and verify_email(ref, cfg).status != "invalid":
            new_id = db.insert("contacts", {"company_id": contact["company_id"], "full_name": result.referral_name,
                                            "first_name": (result.referral_name or "").split()[0] if result.referral_name else None,
                                            "email": ref, "email_status": verify_email(ref, cfg).status,
                                            "email_source": "referral", "channel": "email", "stage": "new",
                                            "notes": f"referral: {contact.get('full_name') or contact.get('first_name') or label}"})
            set_stage(db, cfg, new_id, "cold", "referred by colleague")

    # Draft the answer (the Closer)
    want_reply = intent != "not_interested" or cfg.get("replies.reply_to_not_interested", True)
    if want_reply:
        fresh = db.contact(contact["id"])
        draft = writer.reply_draft(fresh, company, intent, {"subject": mail.subject, "body": mail.body})
        if fresh.get("email"):
            queue_message(db, cfg, fresh, company, draft, "reply", purpose=None, in_reply_to=mail.message_id,
                          needs_human=needs_human, auto_ok=autopilot and intent in SAFE_AUTO_REPLY, channel="email")
        else:
            from . import channels as chn
            avail = chn.available(company, fresh, cfg)
            ch = channel_hint if channel_hint in avail else chn.pick_channel("dm", avail, cfg)
            if ch:
                draft.subject = ""
                queue_message(db, cfg, fresh, company, draft, "reply", purpose=None, needs_human=needs_human,
                              channel=ch, target=avail.get(ch))

    if intent in HOT:
        what = {"ready_to_buy": "wants to go ahead! Mark as WON to start onboarding",
                "meeting_booked": "booked a call", "proposal_request": "asked for a proposal"}.get(intent, INTENTS[intent])
        notify(db, cfg, f"🔥 {company.get('name')}: {what}",
               f"{label} replied: \"{mail.body.strip()[:500]}\"\n\nA reply draft is waiting in your Outbox.")
    db.update("messages", msg_id, {"handled": 1})
    return {"intent": intent, "contact_id": contact["id"], "needs_human": needs_human}


def process_inbox(db: DB, cfg: Config, writer: Writer | None = None, llm: LLM | None = None) -> int:
    try:
        mails = fetch_new(db, cfg)
    except Exception as exc:
        db.log("inbox", f"IMAP check failed: {exc}", level="error")
        return 0
    n = 0
    for mail in mails:
        try:
            if handle_mail(db, cfg, mail, writer, llm):
                n += 1
        except Exception as exc:
            log.exception("failed handling %s", mail.message_id)
            db.log("inbox", f"failed to handle a reply from {mail.from_addr}: {exc}", level="error")
    db.kv_set("last_inbox_at", now_iso())
    return n


def simulate_reply(db: DB, cfg: Config, contact_id: int, text: str, subject: str = "Re: your email",
                   writer: Writer | None = None) -> dict | None:
    """Testing helper: pretend `contact_id` replied with `text`."""
    import secrets
    c = db.contact(contact_id)
    if not c:
        raise ValueError("contact not found")
    last = db.one("SELECT message_id, subject FROM messages WHERE contact_id=? AND direction='out' AND status='sent' "
                  "ORDER BY id DESC LIMIT 1", (contact_id,))
    mail = InboundMail(message_id=f"<sim-{secrets.token_hex(6)}@test>", from_addr=c.get("email") or "",
                       from_name=c.get("full_name") or "", subject=("Re: " + last["subject"]) if last and last["subject"] else subject,
                       body=text, in_reply_to=last["message_id"] if last else "")
    return handle_mail(db, cfg, mail, writer)
