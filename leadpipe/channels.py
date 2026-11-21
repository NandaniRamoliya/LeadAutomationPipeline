"""Channels: how we can reach a lead, and the one-click action for each.

Only EMAIL is sent automatically. WhatsApp, Instagram, LinkedIn, X/Twitter, Facebook and phone calls are
prepared for you as one-click tasks (message written, chat opened with the text filled in where the app allows),
because automating them breaks those platforms' rules (accounts get banned) and, for calls, telemarketing laws.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from .config import Config

ALL_CHANNELS = ["email", "whatsapp", "instagram", "linkedin", "twitter", "facebook", "call"]
DM_CHANNELS = ["whatsapp", "instagram", "linkedin", "twitter", "facebook"]
LABELS = {"email": "Email", "whatsapp": "WhatsApp", "instagram": "Instagram", "linkedin": "LinkedIn",
          "twitter": "X (Twitter)", "facebook": "Facebook", "call": "Phone call"}
ICONS = {"email": "✉️", "whatsapp": "💬", "instagram": "📸", "linkedin": "💼", "twitter": "𝕏", "facebook": "📘", "call": "📞"}


# ---------------------------------------------------------------------------
# Phones
# ---------------------------------------------------------------------------
def parse_phone(raw: str | None, country: str | None = None) -> dict | None:
    """Normalise a phone number. Returns {"e164", "display", "mobile"} or None."""
    if not raw:
        return None
    raw = str(raw).strip()
    if raw.startswith("tel:"):
        raw = raw[4:]
    raw = raw.split(";")[0].split(",")[0].split("/")[0].strip()
    if not re.search(r"\d{6,}", re.sub(r"[\s().-]", "", raw)):
        return None
    try:
        import phonenumbers
        num = phonenumbers.parse(raw, (country or "").upper() or None)
        if not phonenumbers.is_possible_number(num):
            return None
        ntype = phonenumbers.number_type(num)
        mobile = ntype in (phonenumbers.PhoneNumberType.MOBILE, phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE)
        return {"e164": phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164),
                "display": phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
                "mobile": mobile,
                "country": phonenumbers.region_code_for_number(num)}
    except Exception:
        digits = re.sub(r"\D", "", raw)
        if len(digits) < 8:
            return None
        e164 = "+" + digits if raw.strip().startswith("+") else None
        return {"e164": e164, "display": raw, "mobile": False, "country": None} if e164 else None


# ---------------------------------------------------------------------------
# Social handles
# ---------------------------------------------------------------------------
SOCIAL_PATTERNS = {
    "instagram": re.compile(r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,30})/?", re.I),
    "facebook": re.compile(r"(?:https?://)?(?:www\.|m\.)?(?:facebook|fb)\.com/((?:pages/)?[A-Za-z0-9_.\-/]{2,80})", re.I),
    "linkedin": re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/((?:company|in|school)/[A-Za-z0-9_\-%.]{2,100})/?", re.I),
    "twitter": re.compile(r"(?:https?://)?(?:www\.)?(?:twitter|x)\.com/([A-Za-z0-9_]{2,15})(?:[/?#]|$)", re.I),
    "whatsapp": re.compile(r"(?:https?://)?(?:wa\.me/|api\.whatsapp\.com/send\?phone=|chat\.whatsapp\.com/)\+?(\d{7,15})", re.I),
}
NOT_HANDLES = {"sharer", "share", "intent", "home", "login", "p", "explore", "watch", "tr", "plugins", "dialog",
               "hashtag", "search", "privacy", "policies", "legal", "about", "help", "reel", "reels", "stories",
               "tv", "accounts", "i", "web", "photo.php", "events", "groups", "marketplace"}


def extract_socials(urls_or_text: list[str] | str) -> dict[str, str]:
    """Find social profile URLs/handles in links or text. Returns {channel: canonical_url_or_number}."""
    items = urls_or_text if isinstance(urls_or_text, list) else [urls_or_text]
    found: dict[str, str] = {}
    for item in items:
        if not item:
            continue
        for ch, rx in SOCIAL_PATTERNS.items():
            if ch in found:
                continue
            m = rx.search(item)
            if not m:
                continue
            handle = m.group(1).strip("/")
            first = handle.split("/")[0].lower()
            if first in NOT_HANDLES or (ch != "linkedin" and first.endswith(".php")):
                continue
            found[ch] = canonical(ch, handle)
    return found


def canonical(channel: str, handle_or_url: str) -> str:
    h = (handle_or_url or "").strip()
    if not h:
        return ""
    if channel == "whatsapp":
        return re.sub(r"\D", "", h)
    if h.startswith("http"):
        return h.split("?")[0].rstrip("/")
    h = h.lstrip("@").strip("/")
    base = {"instagram": "https://instagram.com/", "facebook": "https://facebook.com/", "linkedin": "https://linkedin.com/",
            "twitter": "https://x.com/"}[channel]
    if channel == "linkedin" and not h.startswith(("company/", "in/", "school/")):
        h = "company/" + h
    return base + h


def handle_of(url: str) -> str:
    return (url or "").rstrip("/").split("/")[-1]


# ---------------------------------------------------------------------------
# What's available for a lead, and the action link
# ---------------------------------------------------------------------------
def available(company: dict, contact: dict | None = None, cfg: Config | None = None) -> dict[str, str]:
    """{channel: target} for every channel we can use for this lead (and that is enabled)."""
    contact = contact or {}
    enabled = set((cfg.get("channels.enabled") if cfg else None) or ALL_CHANNELS)
    out: dict[str, str] = {}
    if contact.get("email"):
        out["email"] = contact["email"]
    phone = parse_phone(contact.get("phone") or company.get("phone"), company.get("country"))
    wa = company.get("whatsapp")
    if wa:
        out["whatsapp"] = re.sub(r"\D", "", wa)
    elif phone and phone["mobile"] and phone["e164"]:
        out["whatsapp"] = phone["e164"].lstrip("+")
    for ch in ("instagram", "linkedin", "twitter", "facebook"):
        val = contact.get(ch) if ch == "linkedin" and contact.get("linkedin") else company.get(ch)
        if val:
            out[ch] = canonical(ch, val)
    if phone and phone["e164"]:
        out["call"] = phone["e164"]
    return {k: v for k, v in out.items() if k in enabled and v}


def pick_channel(step_channel: str, avail: dict[str, str], cfg: Config) -> str | None:
    """Resolve a sequence step's channel ('email', 'call', 'dm', 'auto', or a specific DM app) for this lead."""
    order = [c for c in (cfg.get("channels.dm_order") or DM_CHANNELS) if c in DM_CHANNELS]
    step_channel = (step_channel or "email").lower()
    if step_channel == "email":
        if "email" in avail:
            return "email"
        step_channel = "dm"  # no email address: fall back to a DM, then a call
    if step_channel in DM_CHANNELS:
        if step_channel in avail:
            return step_channel
        step_channel = "dm"
    if step_channel in ("dm", "auto"):
        for ch in order:
            if ch in avail:
                return ch
        return "call" if "call" in avail else None
    if step_channel == "call":
        return "call" if "call" in avail else None
    return None


def action_url(channel: str, target: str, text: str = "") -> str:
    """The link that starts the conversation (prefilled where the app supports it)."""
    if channel == "email":
        return f"mailto:{target}"
    if channel == "whatsapp":
        return f"https://wa.me/{target}" + (f"?text={quote(text)}" if text else "")
    if channel == "call":
        return f"tel:{target}"
    if channel == "instagram":
        return f"https://ig.me/m/{handle_of(target)}"
    if channel == "facebook":
        return f"https://m.me/{handle_of(target)}"
    return target  # linkedin / twitter: open the profile, paste the copied message
