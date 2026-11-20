"""Email verification: syntax -> disposable list -> DNS MX -> role check -> (optional) SMTP probe / Hunter.

Statuses:
  valid    deliverable domain, found on their site / listing / your import
  risky    role inbox (info@, contact@) or catch-all. Usually fine for small local businesses
  guess    pattern-generated (firstname@domain). Only sent if enrichment.allow_guessed_emails
  invalid  bad syntax, disposable, or the domain can't receive mail. Never sent
"""
from __future__ import annotations

import re
import smtplib
import socket
from dataclasses import dataclass
from functools import lru_cache

import requests

from ..config import Config

EMAIL_RE = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]{1,63})+$", re.I)
ROLE_PREFIXES = {"info", "contact", "hello", "hi", "admin", "office", "support", "sales", "enquiry", "enquiries",
                 "inquiry", "reception", "mail", "team", "booking", "bookings", "appointments", "appointment", "help",
                 "care", "customercare", "service", "frontdesk", "general", "clinic", "studio", "marketing"}
NEVER_EMAIL_PREFIXES = {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster", "abuse",
                        "privacy", "legal", "dpo", "gdpr", "unsubscribe", "webmaster", "hostmaster", "jobs", "careers",
                        "hr", "billing", "accounts", "invoice", "invoices", "press", "media"}
DISPOSABLE = {"mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "temp-mail.org",
              "yopmail.com", "trashmail.com", "sharklasers.com", "getnada.com", "dispostable.com", "maildrop.cc"}
PLACEHOLDER_DOMAINS = {"example.com", "example.org", "domain.com", "email.com", "yourdomain.com", "sentry.io",
                       "wixpress.com", "sentry-next.wixpress.com", "godaddy.com", "test.com"}


@dataclass
class Verification:
    status: str
    reason: str


@lru_cache(maxsize=4096)
def mx_hosts(domain: str) -> tuple[str, ...]:
    try:
        import dns.resolver
        answers = dns.resolver.resolve(domain, "MX", lifetime=8)
        return tuple(str(r.exchange).rstrip(".") for r in sorted(answers, key=lambda r: r.preference))
    except Exception:
        try:
            import dns.resolver
            dns.resolver.resolve(domain, "A", lifetime=8)
            return (domain,)  # implicit MX
        except Exception:
            return ()


def _smtp_probe(email: str, mx: str, helo_domain: str) -> str | None:
    try:
        with smtplib.SMTP(mx, 25, timeout=10) as s:
            s.helo(helo_domain)
            s.mail(f"check@{helo_domain}")
            code, _ = s.rcpt(email)
            if code in (250, 251):
                # catch-all test
                c2, _ = s.rcpt(f"zz-no-such-user-8271@{email.split('@')[1]}")
                return "catch_all" if c2 in (250, 251) else "ok"
            if code >= 500:
                return "rejected"
    except (OSError, smtplib.SMTPException, socket.timeout):
        return None
    return None


def verify_email(email: str, cfg: Config, guessed: bool = False) -> Verification:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        return Verification("invalid", "bad syntax")
    local, domain = email.rsplit("@", 1)
    if domain in DISPOSABLE or domain in PLACEHOLDER_DOMAINS:
        return Verification("invalid", "disposable/placeholder domain")
    if local in NEVER_EMAIL_PREFIXES or local.startswith(("noreply", "no-reply")):
        return Verification("invalid", "not a person/sales inbox")
    hosts = mx_hosts(domain)
    if not hosts:
        return Verification("invalid", "domain cannot receive email (no MX)")

    hunter_key = cfg.env("HUNTER_API_KEY")
    if hunter_key:
        try:
            r = requests.get("https://api.hunter.io/v2/email-verifier", params={"email": email, "api_key": hunter_key},
                             timeout=20)
            if r.ok:
                result = (r.json().get("data") or {}).get("status")
                if result == "valid":
                    return Verification("valid", "hunter: valid")
                if result == "invalid":
                    return Verification("invalid", "hunter: invalid")
                if result in ("accept_all", "unknown", "webmail"):
                    pass
        except requests.RequestException:
            pass

    if cfg.get("enrichment.smtp_probe", False):
        probe = _smtp_probe(email, hosts[0], cfg.sending_domain)
        if probe == "rejected":
            return Verification("invalid", "mailbox rejected by server")
        if probe == "ok":
            return Verification("valid", "mailbox confirmed by server")
        if probe == "catch_all" and guessed:
            return Verification("guess", "catch-all domain, guessed address")

    if guessed:
        return Verification("guess", "pattern guess, MX ok")
    if local in ROLE_PREFIXES:
        return Verification("risky", "role inbox (info@/contact@)")
    return Verification("valid", "MX ok")


def is_sendable(status: str | None, cfg: Config) -> bool:
    if status == "valid":
        return True
    if status == "risky":
        return bool(cfg.get("enrichment.allow_role_emails", True))
    if status == "guess":
        return bool(cfg.get("enrichment.allow_guessed_emails", False))
    return False
