"""Reads a reply and decides what it means (intent).

Safety-critical intents (bounce, auto-reply, unsubscribe) are always decided by deterministic rules.
Everything else uses the AI when available, with keyword rules as the fallback.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .config import Config
from .llm import LLM

INTENTS = {
    "unsubscribe": "Asked to stop / remove them",
    "bounce": "Delivery failure (bad address)",
    "auto_reply": "Out-of-office / automatic reply",
    "not_interested": "Clear no",
    "not_now": "Maybe later / bad timing",
    "already_have": "Already has someone / in-house",
    "interested": "Positive: wants to know more",
    "price_question": "Asked about price",
    "portfolio_request": "Asked for portfolio / examples",
    "timeline_question": "Asked how long it takes",
    "proposal_request": "Asked for a proposal / quote",
    "meeting_booked": "Booked or confirmed a call",
    "ready_to_buy": "Wants to go ahead (client says yes)",
    "referral": "Pointed you to someone else",
    "question": "Other question",
    "other": "Unclear: needs you",
}
NEEDS_HUMAN = {"ready_to_buy", "proposal_request", "referral", "question", "other"}
HOT = {"interested", "price_question", "portfolio_request", "timeline_question", "proposal_request",
       "meeting_booked", "ready_to_buy", "referral", "question"}

QUOTE_MARKERS = [
    re.compile(r"^\s*On .{0,200}wrote:\s*$", re.M | re.S),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}", re.M | re.I),
    re.compile(r"^\s*From:\s.+$", re.M),
    re.compile(r"^\s*Sent from my (iPhone|Android|phone|mobile)", re.M | re.I),
    re.compile(r"^\s*_{5,}\s*$", re.M),
]


def strip_quoted(text: str) -> str:
    text = (text or "").replace("\r\n", "\n")
    cut = len(text)
    for rx in QUOTE_MARKERS:
        m = rx.search(text)
        if m and m.start() < cut:
            cut = m.start()
    lines = [ln for ln in text[:cut].split("\n") if not ln.lstrip().startswith(">")]
    return "\n".join(lines).strip()


def _any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


@dataclass
class Classification:
    intent: str
    confidence: float
    source: str = "rules"
    followup_in_days: int | None = None
    referral_email: str | None = None
    referral_name: str | None = None
    summary: str = ""
    extra: dict = field(default_factory=dict)


BOUNCE_SUBJECTS = [r"undeliver", r"delivery status notification", r"mail delivery (failed|subsystem)", r"returned mail",
                   r"failure notice", r"delivery failure", r"could not be delivered", r"address not found"]
AUTO_SUBJECTS = [r"out of (the )?office", r"automatic reply", r"auto[- ]?reply", r"autoreply", r"on vacation", r"away from",
                 r"on leave", r"annual leave", r"maternity leave", r"\bOOO\b"]
UNSUB = [r"\bunsubscribe\b", r"\bremove me\b", r"\btake me off\b", r"\bstop (e-?mailing|contacting|sending|messaging)\b",
         r"\bdo not (contact|e-?mail|message)\b", r"\bdon'?t (contact|e-?mail|message)\b", r"\bopt[- ]?out\b",
         r"\bno more (e-?mails|messages)\b", r"\bthis is spam\b", r"\breported as spam\b", r"^\s*stop\.?\s*$",
         r"\bleave me alone\b", r"\bremove (us|this email|my email)\b"]
NOT_INTERESTED = [r"\bnot interested\b", r"\bno,? thanks?\b", r"\bno,? thank you\b", r"\bnot for us\b",
                  r"\bwe'?re (all )?good\b", r"\bwe are (all )?good\b", r"\bnot looking\b", r"\bpass on this\b",
                  r"\bno need\b", r"\bnot required\b", r"\bnot needed\b", r"^\s*no\.?\s*$", r"\bplease don'?t\b"]
ALREADY_HAVE = [r"\balready (have|work with|working with|got|use|using)\b", r"\bwe have (a|an|our own) (developer|agency|team|guy|person|designer|vendor)\b",
                r"\bin[- ]house\b", r"\bour (developer|agency|team|it team|designer|marketing team) (handles|takes care|does)\b"]
NOT_NOW = [r"\bnot (right )?now\b", r"\bnot interested (right now|at the moment|for now|currently|this year)\b", r"\bmaybe later\b", r"\blater this year\b", r"\bnext (month|quarter|year)\b",
           r"\bbusy (right now|at the moment|these days)\b", r"\bcircle back\b", r"\b(reach out|get back|contact me|ping me) (again )?in\b",
           r"\b(few|couple of|couple) (weeks|months)\b", r"\bnot at the moment\b", r"\bno budget\b", r"\bbad time\b",
           r"\bafter (diwali|christmas|the holidays|new year|ramadan|eid)\b", r"\bnot a priority\b", r"\bin (\d+|a few|two|three|six) (weeks|months)\b"]
READY = [r"\blet'?s (move forward|go ahead|proceed|do (it|this)|start|get started|begin)\b", r"\bwe'?d like to (proceed|go ahead|start)\b",
         r"\bsend (me |us )?(the )?(invoice|agreement|contract|payment link)\b", r"\bwhere do (i|we) sign\b",
         r"\bhow (do|can) (i|we) pay\b", r"\bplease (proceed|go ahead|start)\b", r"\bit'?s a deal\b",
         r"\bwe('re| are) ready to (start|go|begin|proceed)\b"]
PROPOSAL = [r"\bsend (me |us )?(a |your )?(proposal|quotation|quote|estimate)\b", r"\b(share|email) (a |the |your )?(proposal|quote|quotation)\b"]
MEETING = [r"\b(i'?ve |i have |just )?booked\b", r"\bscheduled (a|the) (call|meeting|slot)\b", r"\bsee you (on|then|tomorrow)\b",
           r"\bcalendar invite\b", r"\bconfirmed for\b", r"\bpicked a (slot|time)\b", r"\bbooked a (slot|call|time)\b"]
PRICE = [r"\bhow much\b", r"\bpric(e|es|ing)\b", r"\bcost(s|ing)?\b", r"\bcharges?\b", r"\brates?\b", r"\bfees?\b",
         r"\bbudget\b", r"\bkitna\b", r"\bpaise\b"]
PORTFOLIO = [r"\bportfolio\b", r"\bexamples?\b", r"\bsamples?\b", r"\bprevious work\b", r"\bpast work\b", r"\bcase stud",
             r"\breferences?\b", r"\byour work\b", r"\bwebsites? you('ve| have) (made|built|done)\b"]
TIMELINE = [r"\bhow long\b", r"\bhow much time\b", r"\btimeline\b", r"\bturnaround\b", r"\bhow soon\b", r"\bwhen can you\b",
            r"\bhow many days\b"]
REFERRAL = [r"\b(contact|email|reach out to|speak (to|with)|talk (to|with)|write to)\s+(my|our|the)\s+\w+",
            r"\bright person (is|would be)\b", r"\b(cc'?d|copying|looping in|forwarded (this|it) to)\b", r"\bhandles (this|that|our)\b"]
INTERESTED = [r"\binterested\b", r"^\s*(yes|yeah|yep|sure|ok(ay)?|haan|ha)\b", r"\bsounds (good|great|interesting)\b",
              r"\btell me more\b", r"\bplease send\b", r"\bsend (it|it over|the (audit|video|mock-?up|details))\b",
              r"\blet'?s (talk|chat|connect)\b", r"\bcall me\b", r"\bhappy to (chat|talk)\b", r"\bwould love\b",
              r"\bgo on\b", r"\bmore (details|info|information)\b", r"\bwhat do you (suggest|propose)\b"]
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,24}")


def followup_days(text: str) -> int:
    t = text.lower()
    m = re.search(r"\bin (\d+|a few|two|three|four|six) (week|month)s?\b", t)
    words = {"a few": 3, "two": 2, "three": 3, "four": 4, "six": 6}
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else words.get(m.group(1), 2)
        return n * (7 if m.group(2) == "week" else 30)
    if "next week" in t:
        return 7
    if "next month" in t:
        return 30
    if "next quarter" in t:
        return 90
    if "next year" in t:
        return 180
    return 60


def rule_classify(body: str, subject: str = "", headers: dict | None = None, from_addr: str = "",
                  own_domain: str = "") -> Classification:
    headers = {k.lower(): str(v) for k, v in (headers or {}).items()}
    fa = (from_addr or "").lower()
    if (fa.startswith(("mailer-daemon", "postmaster")) or _any(BOUNCE_SUBJECTS, subject)
            or "report-type=delivery-status" in headers.get("content-type", "").replace(" ", "").lower()):
        return Classification("bounce", 0.99)
    auto = headers.get("auto-submitted", "no").lower()
    if (auto not in ("", "no") or "x-autoreply" in headers or "x-autorespond" in headers
            or headers.get("precedence", "").lower() in ("auto_reply",) or _any(AUTO_SUBJECTS, subject)):
        return Classification("auto_reply", 0.95, followup_in_days=None)

    text = strip_quoted(body)
    low = text.lower()
    if _any(UNSUB, low):
        return Classification("unsubscribe", 0.97)
    if _any(READY, low):
        return Classification("ready_to_buy", 0.75)
    if _any(MEETING, low):
        return Classification("meeting_booked", 0.75)
    if _any(ALREADY_HAVE, low):
        return Classification("already_have", 0.8)
    if _any(NOT_NOW, low):
        return Classification("not_now", 0.8, followup_in_days=followup_days(low))
    if _any(NOT_INTERESTED, low):
        return Classification("not_interested", 0.85)
    if _any(PROPOSAL, low):
        return Classification("proposal_request", 0.75)
    if _any(REFERRAL, low):
        emails = [e.lower() for e in EMAIL_RE.findall(text) if not own_domain or not e.lower().endswith(own_domain)]
        return Classification("referral", 0.7, referral_email=emails[0] if emails else None)
    if _any(PRICE, low):
        return Classification("price_question", 0.75)
    if _any(TIMELINE, low):
        return Classification("timeline_question", 0.75)
    if _any(PORTFOLIO, low):
        return Classification("portfolio_request", 0.75)
    if _any(INTERESTED, low):
        return Classification("interested", 0.7)
    if "?" in low:
        return Classification("question", 0.5)
    return Classification("other", 0.3)


CLASSIFY_SYSTEM = ("You classify replies to a freelancer's sales emails. Pick exactly one intent. If the person asks to "
                   "stop or be removed, it is 'unsubscribe'. 'ready_to_buy' only when they clearly want to start / pay / "
                   "sign. 'meeting_booked' only when a call time is booked or confirmed. Extract a follow-up delay in "
                   "days if they say 'later' (else null), and a referred person's email/name if any (else null).")
CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": [i for i in INTENTS if i not in ("bounce", "auto_reply")]},
        "confidence": {"type": "number"},
        "followup_in_days": {"type": ["integer", "null"]},
        "referral_email": {"type": ["string", "null"]},
        "referral_name": {"type": ["string", "null"]},
        "summary": {"type": "string"},
    },
    "required": ["intent", "confidence", "followup_in_days", "referral_email", "referral_name", "summary"],
    "additionalProperties": False,
}


def classify(cfg: Config, body: str, subject: str = "", headers: dict | None = None, from_addr: str = "",
             llm: LLM | None = None) -> Classification:
    rules = rule_classify(body, subject, headers, from_addr, cfg.sending_domain)
    if rules.intent in ("bounce", "auto_reply", "unsubscribe"):
        return rules
    if llm is None or not llm.available:
        return rules
    out = llm.json(CLASSIFY_SYSTEM, json.dumps({"subject": subject, "reply": strip_quoted(body)[:4000]}, ensure_ascii=False),
                   CLASSIFY_SCHEMA, effort=str(cfg.get("llm.effort_classify", "low")))
    if not out or out.get("intent") not in INTENTS:
        return rules
    if out["intent"] == "unsubscribe" or float(out.get("confidence") or 0) >= 0.5:
        return Classification(out["intent"], float(out.get("confidence") or 0.6), "ai",
                              followup_in_days=out.get("followup_in_days") or (rules.followup_in_days if out["intent"] == "not_now" else None),
                              referral_email=(out.get("referral_email") or rules.referral_email),
                              referral_name=out.get("referral_name"), summary=out.get("summary") or "")
    return rules
