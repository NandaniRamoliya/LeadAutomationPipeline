"""Pre-send quality and safety checks. Every email passes through `check_message` before it can go out.

Rules are taken from the playbooks:
  * first email: short, one observation, one soft question, no pitch, no price, no links
  * no "Dear Sir/Madam", no fake compliments, no generic AI phrases, no "just checking in"
  * no misleading subject lines, minimal links, easy opt-out, real postal address
"""
from __future__ import annotations

import re

from .config import Config

# Phrases that scream "template" or "AI wrote this". Hard fail.
BANNED_PHRASES = [
    "dear sir", "dear madam", "dear sir/madam", "to whom it may concern",
    "i hope this email finds you well", "i hope this finds you well", "hope you are doing well",
    "i hope you're doing well", "hope this message finds you",
    "just checking in", "just following up", "just circling back", "touching base",
    "synergy", "synergies", "leverage our", "game-changer", "game changer", "revolutionize",
    "revolutionise", "cutting-edge", "cutting edge", "state-of-the-art", "world-class",
    "delve", "in today's fast-paced", "in today's digital", "unlock the power", "unlock your",
    "take your business to the next level", "next level", "elevate your", "seamless", "supercharge",
    "i came across your profile and was impressed", "i was blown away", "amazing work",
    "we are a leading", "we are the best", "best-in-class", "one-stop solution", "one stop solution",
    "as an ai", "i'd love to pick your brain", "quick call to explore", "explore synergies",
    "act now", "limited time", "risk-free", "100% guaranteed", "guaranteed results", "no obligation",
    "click here", "buy now",
]

# Leftover placeholders like [Name], {{first_name}}, {company}, <COMPANY>
MERGE_TAG_RE = re.compile(r"\[[A-Z][A-Za-z _/-]{1,30}\]|\{\{.*?\}\}|\{[a-z_]{2,30}\}|<[A-Z_]{3,30}>")
URL_RE = re.compile(r"(https?://|www\.)\S+", re.I)
PRICE_RE = re.compile(r"(₹|\$|€|£|\brs\.?\s?\d|\binr\b|\busd\b|\beur\b|\bgbp\b|\bper month\b|/month\b|\bper hour\b)", re.I)
SHOUT_RE = re.compile(r"\b[A-Z]{5,}\b")

# Word limits per purpose (body only, signature/footer excluded).
WORD_LIMITS = {
    "intro": 95, "bump": 50, "insight": 100, "value": 100, "breakup": 70,
    "booking_nudge": 70, "proof": 100, "close_loop": 70,
    "proposal_followup": 90, "objection_preempt": 120, "decision_nudge": 80, "close_file": 70,
    "nurture_value": 100, "nurture_idea": 100, "nurture_final": 70,
    "care_checkin": 90, "care_feedback": 100, "care_referral": 100, "care_next": 110,
    "referral_intro": 100,
}
FIRST_TOUCH = {"intro", "referral_intro"}


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text or ""))


def check_message(cfg: Config, subject: str, body: str, purpose: str | None, company_name: str = "",
                  kind: str = "sequence") -> list[dict]:
    """Return a list of issues: {"level": "block"|"warn", "rule": str, "detail": str}."""
    issues: list[dict] = []
    text = f"{subject}\n{body}"
    low = text.lower()

    def add(level: str, rule: str, detail: str) -> None:
        issues.append({"level": level, "rule": rule, "detail": detail})

    if not (body or "").strip():
        add("block", "empty_body", "The email body is empty.")
    if kind != "reply" and not (subject or "").strip():
        add("block", "empty_subject", "The subject line is empty.")

    tags = MERGE_TAG_RE.findall(text)
    if tags:
        add("block", "merge_tags", f"Unfilled placeholders: {', '.join(sorted(set(tags))[:5])}")

    banned = [p for p in BANNED_PHRASES + list(cfg.get("compliance.extra_banned_phrases", []) or [])
              if p and p.lower() in low]
    if banned:
        add("block", "banned_phrase", f"Sounds like spam / AI template: {', '.join(banned[:5])}")

    limit = WORD_LIMITS.get(purpose or "", 160 if kind in ("reply", "onboarding") else 120)
    words = word_count(body)
    if words > limit:
        add("block" if words > limit * 1.3 else "warn", "too_long", f"{words} words (limit {limit}). Shorter gets more replies.")

    links = len(URL_RE.findall(body or ""))
    max_links = int(cfg.get("compliance.max_links_first_email", 0)) if purpose in FIRST_TOUCH else \
        int(cfg.get("compliance.max_links_followup", 1))
    if kind in ("reply", "onboarding"):
        max_links = 4
    if links > max_links:
        add("block", "too_many_links", f"{links} links (max {max_links} for this email). Links hurt deliverability.")

    if purpose in FIRST_TOUCH and PRICE_RE.search(body or ""):
        add("block", "price_in_first_email", "No prices in the first message. Price comes after interest.")

    if subject:
        if len(subject) > 70:
            add("warn", "long_subject", f"Subject is {len(subject)} characters. Aim for under 50.")
        if purpose in FIRST_TOUCH and re.match(r"^\s*(re|fw|fwd)\s*:", subject, re.I):
            add("block", "misleading_subject", "A first email must not pretend to be a reply/forward (Re:/Fwd:).")
        if subject.isupper() and len(subject) > 4:
            add("block", "shouting_subject", "Subject is all capitals.")
    if body.count("!") > 2:
        add("warn", "exclamation", "More than two exclamation marks.")
    if len(SHOUT_RE.findall(body or "")) > 2:
        add("warn", "caps", "Several words in ALL CAPS.")

    if purpose in FIRST_TOUCH and company_name:
        first_word = company_name.split()[0].lower() if company_name.split() else ""
        if first_word and first_word not in low:
            add("warn", "not_personal", "The company name isn't mentioned. It should read as if written only for them.")
    if purpose in FIRST_TOUCH and "?" not in (body or ""):
        add("warn", "no_question", "End with one simple yes/no question.")
    return issues


def blocking(issues: list[dict]) -> bool:
    return any(i["level"] == "block" for i in issues)


def footer(cfg: Config, kind: str = "sequence", unsubscribe_url: str | None = None) -> str:
    """Signature + legal footer appended at send time (never written by the AI)."""
    parts = [(cfg.get("profile.signature") or cfg.from_name or "").rstrip()]
    if kind in ("sequence",):
        if cfg.get("compliance.include_address", True) and cfg.get("profile.physical_address"):
            parts.append(str(cfg.get("profile.physical_address")))
        opt = cfg.get("compliance.opt_out_line")
        if opt:
            parts.append(str(opt))
        if unsubscribe_url:
            parts.append(f"Unsubscribe: {unsubscribe_url}")
    return "\n\n".join(p for p in parts if p)
