"""Reads your mailbox over IMAP (read-only: never deletes, moves, or marks anything as read)."""
from __future__ import annotations

import email
import imaplib
import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parseaddr

from bs4 import BeautifulSoup

from .config import Config
from .db import DB, now_iso, utcnow

log = logging.getLogger("leadpipe.inbox")
FINAL_RCPT_RE = re.compile(r"(?:Final|Original)-Recipient:\s*rfc822;\s*<?([^\s>]+@[^\s>]+)>?", re.I)
KEEP_HEADERS = ("auto-submitted", "x-autoreply", "x-autorespond", "precedence", "content-type", "x-failed-recipients")


@dataclass
class InboundMail:
    message_id: str
    from_addr: str
    from_name: str
    subject: str
    body: str
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)
    headers: dict = field(default_factory=dict)
    dsn_recipients: list[str] = field(default_factory=list)
    to_addrs: list[str] = field(default_factory=list)
    cc_addrs: list[str] = field(default_factory=list)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _body(msg: Message) -> tuple[str, list[str]]:
    plain, html, dsn = [], [], []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if part.get_content_disposition() == "attachment":
            continue
        if ctype in ("message/delivery-status",):
            payload = part.get_payload()
            text = "\n".join(str(p) for p in payload) if isinstance(payload, list) else str(payload)
            dsn += FINAL_RCPT_RE.findall(text)
            continue
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            raw = part.get_payload(decode=True) or b""
            text = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        (plain if ctype == "text/plain" else html).append(text)
    body = "\n".join(plain).strip()
    if not body and html:
        body = BeautifulSoup("\n".join(html), "html.parser").get_text("\n", strip=True)
    dsn += FINAL_RCPT_RE.findall(body)
    failed = msg.get("X-Failed-Recipients")
    if failed:
        dsn += [a.strip() for a in failed.split(",")]
    return body, [d.lower() for d in dict.fromkeys(dsn)]


def parse_message(raw: bytes) -> InboundMail:
    msg = email.message_from_bytes(raw)
    name, addr = parseaddr(_decode(msg.get("From")))
    body, dsn = _body(msg)
    refs = (msg.get("References") or "").split()
    return InboundMail(
        message_id=(msg.get("Message-ID") or "").strip(),
        from_addr=addr.lower(), from_name=name, subject=_decode(msg.get("Subject")), body=body,
        in_reply_to=(msg.get("In-Reply-To") or "").strip(), references=refs,
        headers={k: msg.get(k) for k in KEEP_HEADERS if msg.get(k)}, dsn_recipients=dsn,
        to_addrs=[a.lower() for _, a in getaddresses(msg.get_all("To", []))],
        cc_addrs=[a.lower() for _, a in getaddresses(msg.get_all("Cc", []))],
    )


def fetch_new(db: DB, cfg: Config, since_days: int = 4, limit: int = 200) -> list[InboundMail]:
    host = cfg.env("IMAP_HOST")
    if not host:
        return []
    port = int(cfg.env("IMAP_PORT", "993") or 993)
    user = cfg.env("IMAP_USER") or cfg.env("SMTP_USER")
    password = cfg.env("IMAP_PASSWORD") or cfg.env("SMTP_PASSWORD")
    folders = [f.strip() for f in (cfg.env("IMAP_FOLDERS", "INBOX") or "INBOX").split(",") if f.strip()]
    since = (utcnow() - timedelta(days=since_days)).strftime("%d-%b-%Y")
    out: list[InboundMail] = []
    conn = imaplib.IMAP4_SSL(host, port, timeout=60)
    try:
        conn.login(user, password)
        for folder in folders:
            typ, _ = conn.select(f'"{folder}"', readonly=True)
            if typ != "OK":
                continue
            typ, data = conn.search(None, f'(SINCE "{since}")')
            if typ != "OK" or not data or not data[0]:
                continue
            ids = data[0].split()[-limit:]
            for num in ids:
                typ, hdr = conn.fetch(num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
                if typ != "OK" or not hdr or not isinstance(hdr[0], tuple):
                    continue
                mid_line = hdr[0][1].decode(errors="replace")
                mid = mid_line.split(":", 1)[-1].strip() if ":" in mid_line else ""
                if mid and (db.scalar("SELECT 1 FROM seen_inbound WHERE message_id=?", (mid,))
                            or db.scalar("SELECT 1 FROM messages WHERE message_id=?", (mid,))):
                    continue
                typ, full = conn.fetch(num, "(BODY.PEEK[])")
                if typ != "OK" or not full or not isinstance(full[0], tuple):
                    continue
                mail = parse_message(full[0][1])
                if not mail.message_id:
                    mail.message_id = f"<no-id-{folder}-{num.decode()}@local>"
                out.append(mail)
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return out


def mark_seen(db: DB, message_id: str) -> None:
    db.execute("INSERT OR IGNORE INTO seen_inbound (message_id, ts) VALUES (?, ?)", (message_id, now_iso()))
