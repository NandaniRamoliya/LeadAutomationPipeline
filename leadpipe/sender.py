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


def todays_cap(db: DB, cfg: Config, now: datetime | None = None) -> int:
    s = cfg.section("sending")
    cap = int(s.get("daily_cap", 40))
    w = s.get("warmup") or {}
    if not w.get("enabled", True):
        return cap
    start_s = w.get("start_date") or db.kv_get("first_send_date")
    if not start_s:
        return min(cap, int(w.get("start_per_day", 5)))
    try:
        start = date.fromisoformat(str(start_s))
    except ValueError:
        return min(cap, int(w.get("start_per_day", 5)))
    days = max((local_today(cfg, now) - start).days, 0)
    return min(cap, int(w.get("start_per_day", 5)) + int(w.get("increase_per_day", 2)) * days)


def sent_today(db: DB, cfg: Config, now: datetime | None = None, first_touch_only: bool = False) -> int:
    a, b = day_bounds_utc(cfg, now)
    extra = " AND kind = 'sequence' AND step = 0 AND sequence = 'cold'" if first_touch_only else ""
    return int(db.scalar(f"SELECT COUNT(*) FROM messages WHERE direction='out' AND channel='email' AND status='sent' "
                         f"AND sent_at >= ? AND sent_at < ?{extra}", (a, b)) or 0)


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
def build_email(db: DB, cfg: Config, msg: dict, contact: dict) -> EmailMessage:
    em = EmailMessage()
    em["From"] = formataddr((cfg.from_name, cfg.from_email or "me@localhost"))
    em["To"] = formataddr((contact.get("full_name") or "", contact["email"]))
    em["Subject"] = msg["subject"] or "(no subject)"
    em["Date"] = format_datetime(utcnow())
    em["Message-ID"] = make_msgid(domain=cfg.sending_domain)
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
        targets = [f"<mailto:{cfg.from_email}?subject=unsubscribe>"]
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

    def send(self, em: EmailMessage) -> None:
        if self.mode != "smtp":
            out = self.cfg.data_dir / "dry_run"
            out.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            safe = "".join(ch for ch in em["To"] if ch.isalnum() or ch in "@._-")[:60]
            (out / f"{stamp}-{safe}-{secrets.token_hex(3)}.eml").write_bytes(bytes(em))
            return
        host = self.cfg.env("SMTP_HOST")
        port = int(self.cfg.env("SMTP_PORT", "587") or 587)
        security = (self.cfg.env("SMTP_SECURITY", "starttls") or "starttls").lower()
        user, password = self.cfg.env("SMTP_USER"), self.cfg.env("SMTP_PASSWORD")
        if not host:
            raise RuntimeError("SMTP_HOST is not set in .env")
        if security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=60)
        else:
            server = smtplib.SMTP(host, port, timeout=60)
        try:
            server.ehlo()
            if security == "starttls":
                server.starttls()
                server.ehlo()
            if user:
                server.login(user, password)
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
    """Send up to `max_send` due emails. The engine calls this every tick, so pace is set by the random gap."""
    if db.kv_get("sending_paused", False):
        return 0
    now = now or utcnow()
    mailer = mailer or Mailer(cfg)
    s = cfg.section("sending")
    if respect_timing:
        nxt = parse_iso(db.kv_get("next_send_at"))
        if nxt and now < nxt:
            return 0
    cap = todays_cap(db, cfg, now)
    if sent_today(db, cfg, now) >= cap:
        return 0
    new_quota = int(s.get("new_leads_per_day", 10))

    rows = db.all(f"""SELECT * FROM messages WHERE direction='out' AND status='approved' AND channel='email'
                      AND (scheduled_for IS NULL OR scheduled_for <= ?) ORDER BY {PRIORITY_SQL}, scheduled_for, id
                      LIMIT 50""", (iso(now),))
    sent = 0
    for msg in rows:
        if sent >= max_send or sent_today(db, cfg, now) >= cap:
            break
        contact = db.contact(msg["contact_id"])
        reason = _hard_check(db, cfg, msg, contact)
        if reason:
            db.update("messages", msg["id"], {"status": "cancelled", "error": reason})
            db.log("send", f"cancelled message {msg['id']}: {reason}", contact_id=msg["contact_id"])
            continue
        company = db.company(contact["company_id"]) or {}
        first_touch = msg["kind"] == "sequence" and msg["sequence"] == "cold" and msg["step"] == 0
        if first_touch and sent_today(db, cfg, now, first_touch_only=True) >= new_quota:
            continue
        if respect_timing and not in_office_hours(cfg, company.get("timezone"), now):
            continue
        if msg["kind"] == "sequence" and s.get("one_company_per_day", True):
            recent = db.scalar("""SELECT 1 FROM messages WHERE company_id=? AND contact_id != ? AND direction='out'
                                  AND status='sent' AND kind='sequence' AND sent_at >= ?""",
                               (contact["company_id"], contact["id"], iso(now - timedelta(hours=20))))
            if recent:
                continue
        issues = compliance.check_message(cfg, msg["subject"] or "", msg["body"] or "", msg["purpose"],
                                          company.get("name", ""), kind=msg["kind"])
        if compliance.blocking(issues):
            db.update("messages", msg["id"], {"status": "pending", "needs_human": 1, "check_issues": json.dumps(issues)})
            db.log("send", f"message {msg['id']} failed checks; moved back to Outbox", level="warn",
                   contact_id=contact["id"])
            continue

        em = build_email(db, cfg, msg, contact)
        try:
            mailer.send(em)
        except smtplib.SMTPRecipientsRefused as exc:
            db.update("messages", msg["id"], {"status": "failed", "error": f"recipient refused: {exc}"[:500]})
            set_stage(db, cfg, contact["id"], "bounced", "recipient refused by mail server")
            continue
        except Exception as exc:
            db.update("messages", msg["id"], {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:500]})
            db.log("send", f"send failed for {contact['email']}: {exc}", level="error", contact_id=contact["id"])
            # Back off for 15 minutes on SMTP trouble so we don't hammer the server.
            db.kv_set("next_send_at", iso(now + timedelta(minutes=15)))
            return sent
        db.update("messages", msg["id"], {"status": "sent", "sent_at": iso(now), "message_id": em["Message-ID"],
                                          "from_addr": cfg.from_email, "to_addr": contact["email"]})
        if not db.kv_get("first_send_date"):
            db.kv_set("first_send_date", local_today(cfg, now).isoformat())
        if msg["kind"] == "sequence":
            advance_after_send(db, cfg, msg)
        else:
            db.update("contacts", contact["id"], {"last_outbound_at": iso(now)})
        sent += 1
        db.log("send", f"{'[dry-run] ' if mailer.mode != 'smtp' else ''}sent '{msg['subject']}' to {contact['email']}",
               contact_id=contact["id"], company_id=contact["company_id"])
        gap = random.uniform(float(s.get("min_gap_minutes", 3)), float(s.get("max_gap_minutes", 9)))
        db.kv_set("next_send_at", iso(now + timedelta(minutes=gap)))
    return sent


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
