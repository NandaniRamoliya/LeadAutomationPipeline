"""Sending mailboxes. The main one comes from .env (FROM_EMAIL, SMTP_*, IMAP_*); add more in config.yaml →
`mailboxes:` to send more per day. Each mailbox warms up on its own and has its own daily cap, and a lead always
hears from the same mailbox (so threads and replies stay together)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from .config import Config


@dataclass
class Mailbox:
    name: str
    email: str
    from_name: str
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: str = "starttls"
    smtp_user: str = ""
    smtp_password: str = ""
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    daily_cap: int | None = None
    warmup_start: str = ""

    @property
    def domain(self) -> str:
        return self.email.split("@")[-1].lower() if "@" in self.email else "localhost"

    @property
    def kv_suffix(self) -> str:
        return "" if self.name == "main" else f":{self.name}"


def mailboxes(cfg: Config) -> list[Mailbox]:
    out: list[Mailbox] = []
    main_email = cfg.from_email
    if main_email or cfg.get("sending.mode", "dry_run") != "smtp":
        out.append(Mailbox(
            name="main", email=main_email or "me@localhost", from_name=cfg.from_name,
            smtp_host=cfg.env("SMTP_HOST"), smtp_port=int(cfg.env("SMTP_PORT", "587") or 587),
            smtp_security=(cfg.env("SMTP_SECURITY", "starttls") or "starttls").lower(),
            smtp_user=cfg.env("SMTP_USER"), smtp_password=cfg.env("SMTP_PASSWORD"),
            imap_host=cfg.env("IMAP_HOST"), imap_port=int(cfg.env("IMAP_PORT", "993") or 993),
            imap_user=cfg.env("IMAP_USER") or cfg.env("SMTP_USER"),
            imap_password=cfg.env("IMAP_PASSWORD") or cfg.env("SMTP_PASSWORD"),
            warmup_start=str(cfg.get("sending.warmup.start_date") or "")))
    for i, m in enumerate(cfg.get("mailboxes") or [], 2):
        if not isinstance(m, dict) or not m.get("email"):
            continue
        pw = os.environ.get(str(m.get("password_env") or ""), "") if m.get("password_env") else ""
        out.append(Mailbox(
            name=str(m.get("name") or f"mailbox{i}"), email=str(m["email"]),
            from_name=str(m.get("from_name") or cfg.from_name),
            smtp_host=str(m.get("smtp_host") or ""), smtp_port=int(m.get("smtp_port") or 587),
            smtp_security=str(m.get("smtp_security") or "starttls").lower(),
            smtp_user=str(m.get("smtp_user") or m["email"]), smtp_password=pw,
            imap_host=str(m.get("imap_host") or ""), imap_port=int(m.get("imap_port") or 993),
            imap_user=str(m.get("imap_user") or m.get("smtp_user") or m["email"]), imap_password=pw,
            daily_cap=int(m["daily_cap"]) if m.get("daily_cap") else None,
            warmup_start=str(m.get("warmup_start_date") or "")))
    return out


def own_addresses(cfg: Config) -> set[str]:
    return {m.email.lower() for m in mailboxes(cfg)}


def by_name(cfg: Config, name: str | None) -> Mailbox | None:
    for m in mailboxes(cfg):
        if m.name == name:
            return m
    return None
