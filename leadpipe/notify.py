"""Alerts to you: email to notifications.owner_email, a Slack/Discord-style webhook (NOTIFY_WEBHOOK_URL),
and/or Telegram (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID). All optional; failures never stop the pipeline."""
from __future__ import annotations

import logging
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import requests

from .config import Config
from .db import DB

log = logging.getLogger("leadpipe.notify")


def notify(db: DB, cfg: Config, title: str, text: str) -> None:
    db.log("notify", f"{title}: {text[:300]}")
    hook = cfg.env("NOTIFY_WEBHOOK_URL")
    if hook:
        try:
            requests.post(hook, json={"text": f"*{title}*\n{text}", "content": f"**{title}**\n{text}"}, timeout=15)
        except requests.RequestException as exc:
            log.warning("webhook notify failed: %s", exc)
    token, chat = cfg.env("TELEGRAM_BOT_TOKEN"), cfg.env("TELEGRAM_CHAT_ID")
    if token and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": f"{title}\n\n{text}"[:4000]}, timeout=15)
        except requests.RequestException as exc:
            log.warning("telegram notify failed: %s", exc)
    owner = cfg.get("notifications.owner_email")
    if owner:
        _email_owner(cfg, owner, title, text)


def _email_owner(cfg: Config, owner: str, title: str, text: str) -> None:
    from .sender import Mailer

    em = EmailMessage()
    em["From"] = formataddr(("Orbit", cfg.from_email or "pipeline@localhost"))
    em["To"] = owner
    em["Subject"] = f"[Orbit] {title}"
    em["Message-ID"] = make_msgid(domain=cfg.sending_domain)
    em["Auto-Submitted"] = "auto-generated"  # so our own inbox reader ignores it
    em.set_content(text + "\n\nOpen your dashboard to review.")
    try:
        Mailer(cfg).send(em)
    except Exception as exc:
        log.warning("owner email failed: %s", exc)
