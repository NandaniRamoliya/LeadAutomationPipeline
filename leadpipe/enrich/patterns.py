"""Learn a company's email address format from addresses it publishes, then apply it to the decision-maker.

`nora.vale@` next to Nora Vale teaches `{first}.{last}`; `nvale@` teaches `{f}{last}`. A lone address that no
named person matches teaches nothing. If two named people would get the same address, no address is produced
(a verifier would confirm the wrong person's mailbox)."""
from __future__ import annotations

import re
import unicodedata

PATTERNS = {
    "first.last": lambda f, l: f"{f}.{l}",
    "firstlast": lambda f, l: f"{f}{l}",
    "first_last": lambda f, l: f"{f}_{l}",
    "f.last": lambda f, l: f"{f[0]}.{l}",
    "flast": lambda f, l: f"{f[0]}{l}",
    "first.l": lambda f, l: f"{f}.{l[0]}",
    "firstl": lambda f, l: f"{f}{l[0]}",
    "last.first": lambda f, l: f"{l}.{f}",
    "lastfirst": lambda f, l: f"{l}{f}",
    "last": lambda f, l: l,
    "first": lambda f, l: f,
}
TITLES = {"dr", "mr", "mrs", "ms", "miss", "prof", "er", "ca", "adv"}


def name_parts(full_name: str) -> tuple[str, str] | None:
    """'Dr. Priya Sharma' -> ('priya', 'sharma'). Needs a first and last name."""
    s = unicodedata.normalize("NFKD", full_name or "").encode("ascii", "ignore").decode()
    words = [w for w in re.split(r"[\s.]+", s.lower()) if w and w not in TITLES and w.isalpha()]
    if len(words) < 2:
        return None
    return words[0], words[-1]


def learn_pattern(emails: list[str], people: list[dict], domain: str) -> str | None:
    """Return the pattern key that explains a published address of a named person, if any."""
    for e in emails:
        local, _, d = e.lower().partition("@")
        if d != domain and not d.endswith("." + domain):
            continue
        for p in people:
            parts = name_parts(p.get("full_name", ""))
            if not parts:
                continue
            for key, fn in PATTERNS.items():
                if key in ("first", "last"):
                    continue  # too ambiguous to learn from
                if fn(*parts) == local:
                    return key
    return None


def apply_pattern(pattern: str, person: dict, people: list[dict], domain: str) -> str | None:
    parts = name_parts(person.get("full_name", ""))
    if not parts or pattern not in PATTERNS:
        return None
    local = PATTERNS[pattern](*parts)
    for other in people:
        if other is person:
            continue
        op = name_parts(other.get("full_name", ""))
        if op and op != parts and PATTERNS[pattern](*op) == local:
            return None  # ambiguous: someone else would own it
    return f"{local}@{domain}"
