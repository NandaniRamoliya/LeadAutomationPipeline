"""`leadpipe doctor`: checks that everything is set up correctly before real emails go out."""
from __future__ import annotations

import imaplib
import json
import smtplib

import requests

from .config import Config
from .db import DB
from .discovery import normalize_domain
from .llm import LLM

OK, WARN, FAIL, INFO = "  OK  ", " WARN ", " FAIL ", " INFO "
PLACEHOLDERS = {"Your Name", "Your Studio", "https://yourstudio.com", "https://cal.com/your-name/15min",
                "Your Studio, Your Street, Your City, PIN, Country"}


def _txt(name: str) -> list[str]:
    try:
        import dns.resolver
        return [b"".join(r.strings).decode(errors="replace") for r in dns.resolver.resolve(name, "TXT", lifetime=8)]
    except Exception:
        return []


def dns_auth(domain: str) -> dict:
    spf = [t for t in _txt(domain) if t.lower().startswith("v=spf1")]
    dmarc = [t for t in _txt(f"_dmarc.{domain}") if t.lower().startswith("v=dmarc1")]
    dkim_sel = None
    for sel in ("google", "selector1", "selector2", "default", "k1", "s1", "s2", "mail", "dkim", "zoho", "smtp", "mx"):
        if any("p=" in t for t in _txt(f"{sel}._domainkey.{domain}")):
            dkim_sel = sel
            break
    return {"spf": spf[0] if spf else None, "dmarc": dmarc[0] if dmarc else None, "dkim_selector": dkim_sel}


def run_doctor(cfg: Config, db: DB, check_network: bool = True) -> bool:
    problems = 0

    def say(level: str, text: str) -> None:
        nonlocal problems
        if level == FAIL:
            problems += 1
        print(f"[{level}] {text}")

    print("== Settings ==")
    say(OK if cfg.path else FAIL, f"config file: {cfg.path or 'config.yaml not found. Run `leadpipe init`'}")
    p = cfg.section("profile")
    left = [k for k, v in p.items() if isinstance(v, str) and v.strip() in PLACEHOLDERS]
    say(WARN if left else OK, "profile filled in" if not left else f"profile still has example values: {', '.join(left)}")
    if not p.get("proof"):
        say(INFO, "no proof listed: emails will skip the credibility line (fine when starting out)")
    targets = cfg.get("targets") or []
    say(OK if targets else FAIL, f"{len(targets)} target(s): " + ", ".join(t.get("name", "?") for t in targets))
    w = [k for k, v in cfg.section("signals").items() if float(v or 0) > 0]
    say(OK if w else FAIL, f"{len(w)} buying signals switched on")

    print("\n== Sending ==")
    mode = cfg.get("sending.mode", "dry_run")
    say(INFO if mode != "smtp" else OK, f"mode: {mode}" + (" (nothing is really sent; emails are saved to data/dry_run)" if mode != "smtp" else ""))
    say(OK if cfg.from_email else (FAIL if mode == "smtp" else WARN), f"from address: {cfg.from_email or 'FROM_EMAIL not set in .env'}")
    site_dom = normalize_domain(p.get("website"))
    if cfg.from_email and site_dom and cfg.sending_domain == site_dom:
        say(WARN, f"you send from your MAIN domain ({site_dom}). Use a separate domain (e.g. get{site_dom}) to protect it.")
    autopilot = db.kv_get("autopilot_cold", cfg.get("approval.autopilot_cold", False))
    say(INFO, f"autopilot for outreach: {'ON' if autopilot else 'OFF (every email waits for your approval)'}")
    w_ = cfg.get("sending.warmup") or {}
    say(INFO, f"daily cap {cfg.get('sending.daily_cap')}, warm-up {'on' if w_.get('enabled', True) else 'OFF'} "
              f"(starts at {w_.get('start_per_day', 5)}/day, +{w_.get('increase_per_day', 2)}/day)")
    if cfg.get("compliance.include_address", True) and (not p.get("physical_address") or p.get("physical_address") in PLACEHOLDERS):
        say(WARN, "physical_address is not set. US law (CAN-SPAM) requires a postal address in commercial email")

    if check_network and cfg.from_email:
        auth = dns_auth(cfg.sending_domain)
        say(OK if auth["spf"] else FAIL, f"SPF for {cfg.sending_domain}: {auth['spf'] or 'MISSING (Gmail will spam you)'}")
        say(OK if auth["dkim_selector"] else WARN, f"DKIM: {'found (selector ' + auth['dkim_selector'] + ')' if auth['dkim_selector'] else 'not found on common selectors. Check your provider'}")
        say(OK if auth["dmarc"] else FAIL, f"DMARC: {auth['dmarc'] or 'MISSING. Add TXT _dmarc.' + cfg.sending_domain + ' = v=DMARC1; p=none;'}")

    if check_network and cfg.env("SMTP_HOST"):
        try:
            port = int(cfg.env("SMTP_PORT", "587") or 587)
            sec = cfg.env("SMTP_SECURITY", "starttls").lower()
            s = smtplib.SMTP_SSL(cfg.env("SMTP_HOST"), port, timeout=20) if sec == "ssl" else smtplib.SMTP(cfg.env("SMTP_HOST"), port, timeout=20)
            s.ehlo()
            if sec == "starttls":
                s.starttls()
                s.ehlo()
            if cfg.env("SMTP_USER"):
                s.login(cfg.env("SMTP_USER"), cfg.env("SMTP_PASSWORD"))
            s.quit()
            say(OK, f"SMTP login to {cfg.env('SMTP_HOST')} works")
        except Exception as exc:
            say(FAIL, f"SMTP login failed: {exc}. Gmail/Workspace needs an App Password")
    elif mode == "smtp":
        say(FAIL, "SMTP_HOST is not set in .env")

    print("\n== Replies ==")
    if not cfg.env("IMAP_HOST"):
        say(WARN, "IMAP_HOST not set: replies won't be read automatically (you can still log them in the dashboard)")
    elif check_network:
        try:
            c = imaplib.IMAP4_SSL(cfg.env("IMAP_HOST"), int(cfg.env("IMAP_PORT", "993") or 993), timeout=20)
            c.login(cfg.env("IMAP_USER") or cfg.env("SMTP_USER"), cfg.env("IMAP_PASSWORD") or cfg.env("SMTP_PASSWORD"))
            c.logout()
            say(OK, f"IMAP login to {cfg.env('IMAP_HOST')} works")
        except Exception as exc:
            say(FAIL, f"IMAP login failed: {exc}")

    print("\n== AI ==")
    llm = LLM(cfg)
    if not llm.available:
        say(INFO, "no AI configured: built-in templates are used (set ANTHROPIC_API_KEY or install the `claude` CLI)")
    elif check_network:
        good, msg = llm.ping()
        say(OK if good else WARN, msg)

    print("\n== Lead sources ==")
    if check_network and "osm" in (cfg.get("discovery.providers") or []):
        try:
            r = requests.get("https://overpass-api.de/api/status", timeout=15)
            say(OK if r.ok else WARN, f"OpenStreetMap Overpass reachable (HTTP {r.status_code})")
        except requests.RequestException as exc:
            say(WARN, f"OpenStreetMap Overpass not reachable: {exc}")
    if "google_places" in (cfg.get("discovery.providers") or []) and not cfg.env("GOOGLE_PLACES_API_KEY"):
        say(FAIL, "google_places provider enabled but GOOGLE_PLACES_API_KEY missing")

    print("\n== Dashboard ==")
    host = cfg.get("dashboard.host", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost") and not cfg.env("DASHBOARD_PASSWORD"):
        say(FAIL, f"dashboard listens on {host} without DASHBOARD_PASSWORD. Set one in .env")
    else:
        say(OK, f"dashboard on http://{host}:{cfg.get('dashboard.port', 8787)}")
    if not cfg.get("dashboard.public_base_url"):
        say(INFO, "public_base_url not set: unsubscribe works by reply (\"no thanks\"); one-click link disabled")

    print("\n" + ("All good." if problems == 0 else f"{problems} problem(s) to fix before sending real emails."))
    return problems == 0


TRAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "service": {"type": "string"}, "service_long": {"type": "string"}, "starter_offer": {"type": "string"},
        "quick_win": {"type": "string"}, "proof": {"type": "array", "items": {"type": "string"}},
        "ideal_customers": {"type": "array", "items": {"type": "string"}},
        "suggested_signals": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["service", "service_long", "starter_offer", "quick_win", "proof", "ideal_customers", "suggested_signals", "notes"],
    "additionalProperties": False,
}


def train_profile(cfg: Config, url: str) -> str:
    from bs4 import BeautifulSoup
    from .enrich.website import HEADERS
    from .templates import SIGNALS

    llm = LLM(cfg)
    if not llm.available:
        return "No AI configured. Fill in the `profile:` section of config.yaml by hand (see docs/USER_GUIDE.md)."
    html = requests.get(url, headers=HEADERS, timeout=20).text
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)[:8000]
    out = llm.json(
        "You help a freelancer set up cold outreach. From their website text, describe what they sell in a business "
        "owner's words, a small low-risk starter offer, a free quick win they could offer, and ONLY proof that the site "
        "actually states (phrase each as a past-tense verb phrase like 'built 12 sites for clinics'; empty list if none). "
        f"Pick suggested_signals only from: {', '.join(SIGNALS)}.",
        json.dumps({"url": url, "site_text": text}), TRAIN_SCHEMA, effort="medium")
    if not out:
        return f"AI call failed: {llm.last_error}"
    import yaml
    snippet = {"profile": {k: out[k] for k in ("service", "service_long", "starter_offer", "quick_win", "proof")}}
    return ("Paste/merge this into config.yaml (check every line, nothing is saved automatically):\n\n"
            + yaml.safe_dump(snippet, sort_keys=False, allow_unicode=True)
            + f"\nIdeal customers: {', '.join(out['ideal_customers'])}\nSignals worth weighting: "
            + ", ".join(out["suggested_signals"]) + (f"\nNotes: {out['notes']}" if out.get("notes") else ""))
