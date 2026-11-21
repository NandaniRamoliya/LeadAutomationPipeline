"""The Sender: sends approved emails one at a time, like a human would.

Guards on every send:
  paused switch · daily cap + warm-up ramp · new-lead quota · recipient office hours · random gap between
  sends · one company per day · suppression list · deliverable address only · stage still matches · checks pass
Priority: replies to humans > onboarding > follow-ups > first emails.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import secrets
import smtplib
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid
from zoneinfo import ZoneInfo

from . import compliance
from .config import Config
from .db import DB, iso, now_iso, parse_iso, utcnow
from .enrich.verify import is_sendable
from .pipeline import TERMINAL_STAGES, advance_after_send, is_suppressed, set_stage

log = logging.getLogger("leadpipe.sender")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def tz(name: str | None, fallback: str = "UTC") -> ZoneInfo:
    try:
        return ZoneInfo(name or fallback)
    except Exception:
        return ZoneInfo(fallback)


def engine_tz(cfg: Config) -> ZoneInfo:
    return tz(cfg.get("engine.timezone"), "UTC")


def local_today(cfg: Config, now: datetime | None = None) -> date:
    return (now or utcnow()).astimezone(engine_tz(cfg)).date()


def in_office_hours(cfg: Config, tzname: str | None, now: datetime | None = None) -> bool:
    oh = cfg.section("sending").get("office_hours") or {}
    local = (now or utcnow()).astimezone(tz(tzname, cfg.get("engine.timezone", "UTC")))
    days = [d.lower()[:3] for d in (oh.get("days") or DAYS[:5])]
    if DAYS[local.weekday()] not in days:
        return False
    start = time.fromisoformat(str(oh.get("start", "09:00")))
    end = time.fromisoformat(str(oh.get("end", "17:30")))
    return start <= local.time() <= end


def day_bounds_utc(cfg: Config, now: datetime | None = None) -> tuple[str, str]:
    z = engine_tz(cfg)
    d = local_today(cfg, now)
    start = datetime.combine(d, time.min, tzinfo=z)
    return iso(start), iso(start + timedelta(days=1))  # type: ignore[return-value]


def todays_cap(db: DB, cfg: Config, now: datetime | None = None, mailbox=None) -> int:
    """Today's limit for one mailbox (warm-up ramp), or for all mailboxes together when mailbox is None."""
    from .mailboxes import mailboxes
    if mailbox is None:
        boxes = mailboxes(cfg)
        return sum(todays_cap(db, cfg, now, m) for m in boxes) if boxes else 0
    s = cfg.section("sending")
    cap = int(mailbox.daily_cap or s.get("daily_cap", 40))
    w = s.get("warmup") or {}
    if not w.get("enabled", True):
        return cap
    start_s = mailbox.warmup_start or db.kv_get("first_send_date" + mailbox.kv_suffix)
    if not start_s:
        return min(cap, int(w.get("start_per_day", 5)))
    try:
        start = date.fromisoformat(str(start_s))
    except ValueError:
        return min(cap, int(w.get("start_per_day", 5)))
    days = max((local_today(cfg, now) - start).days, 0)
    return min(cap, int(w.get("start_per_day", 5)) + int(w.get("increase_per_day", 2)) * days)


def sent_today(db: DB, cfg: Config, now: datetime | None = None, first_touch_only: bool = False, mailbox=None) -> int:
    a, b = day_bounds_utc(cfg, now)
    extra = " AND kind = 'sequence' AND step = 0 AND sequence = 'cold'" if first_touch_only else ""
    params: list = [a, b]
    if mailbox is not None:
        extra += " AND COALESCE(mailbox, 'main') = ?"
        params.append(mailbox.name)
    return int(db.scalar(f"SELECT COUNT(*) FROM messages WHERE direction='out' AND channel='email' AND status='sent' "
                         f"AND sent_at >= ? AND sent_at < ?{extra}", params) or 0)


# ---------------------------------------------------------------------------
# Unsubscribe links (only when dashboard.public_base_url is set)
# ---------------------------------------------------------------------------
def _secret(db: DB, cfg: Config) -> bytes:
    s = cfg.env("LEADPIPE_SECRET") or db.kv_get("secret")
    if not s:
        s = secrets.token_hex(32)
        db.kv_set("secret", s)
    return s.encode()


def unsub_token(db: DB, cfg: Config, contact_id: int) -> str:
    sig = hmac.new(_secret(db, cfg), str(contact_id).encode(), hashlib.sha256).hexdigest()[:20]
    return f"{contact_id}.{sig}"


def verify_unsub_token(db: DB, cfg: Config, token: str) -> int | None:
    try:
        cid_s, sig = token.split(".", 1)
        cid = int(cid_s)
    except ValueError:
        return None
    good = unsub_token(db, cfg, cid).split(".", 1)[1]
    return cid if hmac.compare_digest(good, sig) else None


def unsubscribe_url(db: DB, cfg: Config, contact_id: int) -> str | None:
    base = (cfg.get("dashboard.public_base_url") or "").rstrip("/")
    return f"{base}/u/{unsub_token(db, cfg, contact_id)}" if base else None


# ---------------------------------------------------------------------------
# Building and transmitting
# ---------------------------------------------------------------------------
def build_email(db: DB, cfg: Config, msg: dict, contact: dict, mailbox=None) -> EmailMessage:
    from .mailboxes import mailboxes
    mailbox = mailbox or (mailboxes(cfg) or [None])[0]
    from_email = mailbox.email if mailbox else (cfg.from_email or "me@localhost")
    from_name = mailbox.from_name if mailbox else cfg.from_name
    em = EmailMessage()
    em["From"] = formataddr((from_name, from_email))
    em["To"] = formataddr((contact.get("full_name") or "", contact["email"]))
    em["Subject"] = msg["subject"] or "(no subject)"
    em["Date"] = format_datetime(utcnow())
    em["Message-ID"] = make_msgid(domain=from_email.split("@")[-1])
    reply_to = cfg.env("REPLY_TO")
    if reply_to:
        em["Reply-To"] = reply_to

    # Thread follow-ups and replies under the previous message.
    parent = msg.get("in_reply_to")
    if not parent and (msg.get("subject") or "").lower().startswith("re:"):
        row = db.one("""SELECT message_id FROM messages WHERE contact_id=? AND message_id IS NOT NULL
                        AND status IN ('sent','received') ORDER BY id DESC LIMIT 1""", (contact["id"],))
        parent = row["message_id"] if row else None
    if parent:
        refs = [r["message_id"] for r in db.all("""SELECT message_id FROM messages WHERE contact_id=? AND
                 message_id IS NOT NULL AND status IN ('sent','received') ORDER BY id DESC LIMIT 10""", (contact["id"],))]
        em["In-Reply-To"] = parent
        em["References"] = " ".join(reversed(refs)) or parent

    unsub = unsubscribe_url(db, cfg, contact["id"]) if msg["kind"] == "sequence" else None
    if msg["kind"] == "sequence":
        targets = [f"<mailto:{from_email}?subject=unsubscribe>"]
        if unsub:
            targets.insert(0, f"<{unsub}>")
            em["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        em["List-Unsubscribe"] = ", ".join(targets)
    body = msg["body"].rstrip() + "\n\n" + compliance.footer(cfg, msg["kind"], unsub)
    em.set_content(body)
    return em


class Mailer:
    """SMTP (Gmail / Google Workspace / Microsoft 365 / any provider) or dry_run (.eml files)."""

    def __init__(self, cfg: Config, mode: str | None = None):
        self.cfg = cfg
        self.mode = (mode or cfg.get("sending.mode", "dry_run")).lower()

    def send(self, em: EmailMessage, mailbox=None) -> None:
        if self.mode != "smtp":
            out = self.cfg.data_dir / "dry_run"
            out.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            safe = "".join(ch for ch in em["To"] if ch.isalnum() or ch in "@._-")[:60]
            (out / f"{stamp}-{safe}-{secrets.token_hex(3)}.eml").write_bytes(bytes(em))
            return
        if mailbox is None:
            from .mailboxes import mailboxes
            mailbox = (mailboxes(self.cfg) or [None])[0]
        if mailbox is None or not mailbox.smtp_host:
            raise RuntimeError("SMTP_HOST is not set for this mailbox (.env or config.yaml mailboxes)")
        if mailbox.smtp_security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(mailbox.smtp_host, mailbox.smtp_port, timeout=60)
        else:
            server = smtplib.SMTP(mailbox.smtp_host, mailbox.smtp_port, timeout=60)
        try:
            server.ehlo()
            if mailbox.smtp_security == "starttls":
                server.starttls()
                server.ehlo()
            if mailbox.smtp_user:
                server.login(mailbox.smtp_user, mailbox.smtp_password)
            server.send_message(em)
        finally:
            try:
                server.quit()
            except smtplib.SMTPException:
                pass


# ---------------------------------------------------------------------------
# The send job
# ---------------------------------------------------------------------------
PRIORITY_SQL = """CASE WHEN kind='reply' THEN 0 WHEN kind='onboarding' THEN 1
                       WHEN kind='sequence' AND NOT (sequence='cold' AND step=0) THEN 2 ELSE 3 END"""


def _hard_check(db: DB, cfg: Config, msg: dict, contact: dict | None) -> str | None:
    """Return a reason to cancel this message, or None if it may go."""
    if not contact:
        return "contact deleted"
    if not contact.get("email"):
        return "contact has no email address"
    # One polite answer to someone who said "not interested" is fine; nothing else goes to closed contacts.
    courtesy = msg["kind"] == "reply" and contact["stage"] == "lost"
    if contact["stage"] in TERMINAL_STAGES and not courtesy:
        return f"contact is {contact['stage']}"
    if is_suppressed(db, contact["email"]) and not courtesy:
        return "address is on the do-not-contact list"
    if not is_sendable(contact.get("email_status"), cfg):
        return f"address status '{contact.get('email_status')}' is not deliverable"
    if msg["kind"] == "sequence" and (contact.get("sequence") != msg["sequence"] or contact.get("step") != msg["step"]):
        return "contact moved on (replied or changed stage) before this was sent"
    return None


def send_due(db: DB, cfg: Config, mailer: Mailer | None = None, now: datetime | None = None,
             respect_timing: bool = True, max_send: int = 1) -> int:
    """Send due approved emails: at most `max_send` per mailbox per call. The engine calls this every tick,
    so the pace is set by each mailbox's random gap, warm-up cap and the recipients' office hours."""
    from .mailboxes import by_name, mailboxes
    if db.kv_get("sending_paused", False):
        return 0
    now = now or utcnow()
    mailer = mailer or Mailer(cfg)
    s = cfg.section("sending")
    new_quota = int(s.get("new_leads_per_day", 15))
    boxes = mailboxes(cfg)
    if not boxes:
        return 0
    # Mailboxes with the most room left go first.
    boxes.sort(key=lambda m: todays_cap(db, cfg, now, m) - sent_today(db, cfg, now, mailbox=m), reverse=True)
    rows = db.all(f"""SELECT * FROM messages WHERE direction='out' AND status='approved' AND channel='email'
                      AND (scheduled_for IS NULL OR scheduled_for <= ?) ORDER BY {PRIORITY_SQL}, scheduled_for, id
                      LIMIT 100""", (iso(now),))
    total = 0
    used: set[int] = set()
    for mb in boxes:
        if respect_timing:
            nxt = parse_iso(db.kv_get("next_send_at" + mb.kv_suffix))
            if nxt and now < nxt:
                continue
        cap = todays_cap(db, cfg, now, mb)
        sent = 0
        for msg in rows:
            if msg["id"] in used:
                continue
            if sent >= max_send or sent_today(db, cfg, now, mailbox=mb) >= cap:
                break
            contact = db.contact(msg["contact_id"])
            reason = _hard_check(db, cfg, msg, contact)
            if reason:
                used.add(msg["id"])
                db.update("messages", msg["id"], {"status": "cancelled", "error": reason})
                db.log("send", f"cancelled message {msg['id']}: {reason}", contact_id=msg["contact_id"])
                continue
            owner = contact.get("mailbox")
            if owner and owner != mb.name and by_name(cfg, owner) is not None:
                continue  # this lead belongs to another mailbox
            company = db.company(contact["company_id"]) or {}
            first_touch = msg["kind"] == "sequence" and msg["sequence"] == "cold" and msg["step"] == 0
            if first_touch and sent_today(db, cfg, now, first_touch_only=True) >= new_quota:
                continue
            if respect_timing and not in_office_hours(cfg, company.get("timezone"), now):
                continue
            if msg["kind"] == "sequence" and s.get("one_company_per_day", True):
                recent = db.scalar("""SELECT 1 FROM messages WHERE company_id=? AND contact_id != ? AND direction='out'
                                      AND status='sent' AND kind='sequence' AND channel='email' AND sent_at >= ?""",
                                   (contact["company_id"], contact["id"], iso(now - timedelta(hours=20))))
                if recent:
                    continue
            issues = compliance.check_message(cfg, msg["subject"] or "", msg["body"] or "", msg["purpose"],
                                              company.get("name", ""), kind=msg["kind"])
            if compliance.blocking(issues):
                used.add(msg["id"])
                db.update("messages", msg["id"], {"status": "pending", "needs_human": 1, "check_issues": json.dumps(issues)})
                db.log("send", f"message {msg['id']} failed checks; moved back to your To-do", level="warn",
                       contact_id=contact["id"])
                continue

            used.add(msg["id"])
            em = build_email(db, cfg, msg, contact, mb)
            try:
                mailer.send(em, mb)
            except smtplib.SMTPRecipientsRefused as exc:
                db.update("messages", msg["id"], {"status": "failed", "error": f"recipient refused: {exc}"[:500]})
                set_stage(db, cfg, contact["id"], "bounced", "recipient refused by mail server")
                continue
            except Exception as exc:
                db.update("messages", msg["id"], {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:500]})
                db.log("send", f"[{mb.name}] send failed for {contact['email']}: {exc}", level="error", contact_id=contact["id"])
                # Back off this mailbox for 15 minutes so we don't hammer the server.
                db.kv_set("next_send_at" + mb.kv_suffix, iso(now + timedelta(minutes=15)))
                break
            db.update("messages", msg["id"], {"status": "sent", "sent_at": iso(now), "message_id": em["Message-ID"],
                                              "from_addr": mb.email, "to_addr": contact["email"], "mailbox": mb.name})
            if not owner:
                db.update("contacts", contact["id"], {"mailbox": mb.name})
            if not db.kv_get("first_send_date" + mb.kv_suffix):
                db.kv_set("first_send_date" + mb.kv_suffix, local_today(cfg, now).isoformat())
            if msg["kind"] == "sequence":
                advance_after_send(db, cfg, msg)
            else:
                db.update("contacts", contact["id"], {"last_outbound_at": iso(now)})
            sent += 1
            total += 1
            db.log("send", f"{'[dry-run] ' if mailer.mode != 'smtp' else ''}[{mb.name}] sent '{msg['subject']}' to {contact['email']}",
                   contact_id=contact["id"], company_id=contact["company_id"])
            gap = random.uniform(float(s.get("min_gap_minutes", 3)), float(s.get("max_gap_minutes", 9)))
            db.kv_set("next_send_at" + mb.kv_suffix, iso(now + timedelta(minutes=gap)))
    return total


def mark_manual_done(db: DB, cfg: Config, message_id: int) -> None:
    """You sent a DM / made the call yourself: record it and schedule the next manual step."""
    msg = db.message(message_id)
    if not msg:
        return
    db.update("messages", message_id, {"status": "sent", "sent_at": now_iso()})
    if msg["kind"] == "sequence":
        advance_after_send(db, cfg, msg)
    db.log("manual", f"manual message {message_id} marked as sent", contact_id=msg["contact_id"])


__all__ = ["send_due", "Mailer", "build_email", "todays_cap", "sent_today", "in_office_hours", "mark_manual_done",
           "unsubscribe_url", "verify_unsub_token"]
