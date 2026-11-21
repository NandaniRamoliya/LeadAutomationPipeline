"""The Writer: turns research into one personal email per contact and per follow-up step.

Order of preference:  AI draft that passes every check  ->  AI retry with the failed checks  ->  template.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from . import compliance
from .config import Config
from .db import DB, iso, jloads, parse_iso, utcnow
from .llm import LLM
from . import channels as chn
from .pipeline import is_suppressed, postpone, skip_step, step_info
from .templates import (CALL_TEMPLATES, ONBOARDING_TEMPLATES, REPLY_TEMPLATES, SEQUENCE_TEMPLATES, SIGNALS, Draft,
                        fill, pick, plural)

log = logging.getLogger("leadpipe.writer")

PURPOSE_BRIEFS = {
    "intro": "FIRST email. One specific observation about their business, what it costs them in plain words, "
             "one line of relevant credibility if available, offer the quick win, end with ONE soft yes/no question. "
             "No pitch paragraph, no price, no links. Under 80 words. Subject: 2-6 words, lowercase-ish, specific to them.",
    "bump": "Short follow-up in the same thread (day 3). 1-3 sentences. Add ONE small new detail or the second observation. "
            "Never 'just checking in'.",
    "insight": "Follow-up (day 7). Share a NEW insight: what you'd change first and why it matters for them. Soft question.",
    "value": "Follow-up (day 12). The extra-effort email: offer to make the quick win for them (mini audit, mock-up, "
             "3 concrete fixes). Make it effortless to say yes. One proof link allowed if provided.",
    "breakup": "Final cold email (day 18). Polite close: you won't keep emailing, the door stays open, no guilt-tripping.",
    "booking_nudge": "They said they're interested but haven't booked. Make booking effortless: share the booking link, "
                     "say what they'll get from 15 minutes.",
    "proof": "They're interested but quiet. Share relevant proof (results / portfolio) and offer a call.",
    "close_loop": "Warm lead went quiet. Close the loop politely, keep the door open.",
    "proposal_followup": "After the call / proposal. Ask if anything in the proposal needs changing. Helpful, not pushy.",
    "objection_preempt": "Answer the 2-3 questions people usually have before deciding (time, effort, revisions).",
    "decision_nudge": "Offer a smaller first step (the starter offer) to make the decision easier.",
    "close_file": "Politely close the file; everything discussed still stands.",
    "nurture_value": "They said 'not now' weeks ago. Check back as promised with one useful idea. New subject line.",
    "nurture_idea": "Nurture touch: a fresh, specific idea for their business. Offer a quick mock-up.",
    "nurture_final": "Last nurture touch: final friendly check-in, door open.",
    "care_checkin": "Existing client, project finished 2 weeks ago. Check everything works; offer tweaks.",
    "care_feedback": "Existing client. Ask for a 2-3 line testimonial and honest feedback.",
    "care_referral": "Existing happy client. Ask for an introduction to another business owner with the same problem.",
    "care_next": "Existing client. Suggest the logical next improvement; offer a short plan.",
    "referral_intro": "First email to someone you were referred to. Mention who referred you in the first line.",
    "dm_intro": "FIRST direct message (WhatsApp/Instagram/LinkedIn). Casual and human, 2-3 short sentences, under 50 words: "
                "one specific observation, what it costs, offer the free quick win as a yes/no question. No links, no price.",
    "dm_touch": "Short DM to someone you already emailed: say you emailed, offer the quick win here. Under 35 words.",
    "dm_followup": "Follow-up DM with ONE new small idea. Under 35 words. Never 'just checking in'.",
    "dm_final": "Last DM: friendly close, door open. Under 30 words.",
}
DM_EQUIVALENT = {"intro": "dm_intro", "referral_intro": "dm_intro", "bump": "dm_followup", "insight": "dm_followup",
                 "value": "dm_followup", "breakup": "dm_final"}

WRITER_SYSTEM = """You are a senior freelance sales professional with 20 years of experience writing cold emails \
and follow-ups that get replies from busy business owners. You write ONE short, honest, personal email at a time.

Hard rules:
- Use ONLY facts given in the data. Never invent numbers, results, clients, compliments, or details about the business. \
Anything marked UNVERIFIED must not be stated as fact.
- Lead with THEIR loss, not your skills. Specific > clever. Plain words, short sentences, like a real person typing.
- One email, one ask. Be transparent about why you are writing.
- Every follow-up must add something new. Never "just checking in", "following up", "touching base".
- No "Dear Sir/Madam", no "I hope this finds you well", no hype words (game-changer, next level, seamless, \
cutting-edge, synergy, leverage, revolutionize, delve, unlock).
- No price unless the purpose explicitly allows it. No links in a first email.
- Do NOT write a sign-off, signature, name, postal address or unsubscribe line. They are added automatically.
- Respect the word limit. Start with the given greeting line.
- If subject_mode is "reply", return an empty subject (it is sent as a reply in the same thread)."""

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"],
    "additionalProperties": False,
}


def display_site(url: str | None) -> str:
    if not url:
        return ""
    s = url.split("://", 1)[-1].split("/", 1)[0]
    return s[4:] if s.startswith("www.") else s


def top_signals(cfg: Config, company: dict) -> list[tuple[str, dict]]:
    found = jloads(company.get("signals"), {})
    weights = cfg.section("signals")
    ranked = sorted(((k, v) for k, v in found.items() if k in SIGNALS and float(weights.get(k, 0) or 0) > 0),
                    key=lambda kv: -float(weights.get(kv[0], 0) or 0))
    return ranked


def build_context(cfg: Config, contact: dict, company: dict, extra: dict | None = None) -> dict:
    p = cfg.section("profile")
    first = (contact.get("first_name") or "").strip()
    name = company.get("name") or "your business"
    site = display_site(company.get("website"))
    sigs = top_signals(cfg, company)
    short = name[4:] if name.lower().startswith("the ") else name
    ctx: dict = {
        "first_name": first,
        "greeting": f"Hi {first}," if first else f"Hi {short} team,",
        "greeting_thanks": f"Thanks, {first}." if first else "Thanks for getting back to me.",
        "company": name,
        "website": site or "your website",
        "category": (company.get("category") or "business").replace("_", " "),
        "category_plural": plural(company.get("category") or "local business"),
        "city": company.get("city") or "",
        "service": p.get("service", ""),
        "starter_offer": p.get("starter_offer", ""),
        "starter_offer_cap": (p.get("starter_offer", "") or "")[:1].upper() + (p.get("starter_offer", "") or "")[1:],
        "price_range": p.get("price_range", ""),
        "delivery_time": p.get("delivery_time", ""),
        "booking_link": p.get("booking_link", ""),
        "quick_win": p.get("quick_win", "a quick audit"),
        "your_name": p.get("your_name", ""),
    }
    found = jloads(company.get("signals"), {})
    if any(k in found for k in ("no_website", "instagram_only", "site_broken")):
        # Nothing to audit: offer to show them what a site could look like instead.
        ctx["quick_win"] = p.get("quick_win_no_site") or "a free mock-up of a simple website for you"
    qw = ctx["quick_win"]
    for prefix in ("a free ", "an ", "a ", "free "):
        if qw.lower().startswith(prefix):
            qw = qw[len(prefix):]
            break
    ctx["quick_win_short"] = qw
    proof = [x for x in (p.get("proof") or []) if x]
    # Relevant proof beats impressive proof: use the category-specific line when you have one.
    by_cat = {str(k).lower(): v for k, v in (p.get("proof_by_category") or {}).items()}
    cat_key = (company.get("category") or "").lower()
    if by_cat.get(cat_key):
        proof = [by_cat[cat_key]] + proof
    links = [x for x in (p.get("proof_links") or []) if x]
    ctx["proof1"] = proof[0] if proof else f"have helped other {ctx['category_plural']} with {ctx['service']}"
    ctx["proof_line"] = f"I've {proof[0]}, so I see this a lot. " if proof else ""
    ctx["proof_link_line"] = f"Here's an example of my work: {links[0]}\n\n" if links else ""
    ctx["portfolio_line"] = (f"Here's some recent work: {', '.join(links[:3])}." if links
                             else f"I {ctx['proof1']}, and I'm happy to share examples on a quick call.")

    def sig_text(idx: int, field: str) -> str:
        if idx >= len(sigs):
            return ""
        key, evidence = sigs[idx]
        local = dict(ctx)
        if isinstance(evidence, dict):
            local.update({k: v for k, v in evidence.items() if isinstance(v, (str, int, float))})
        local.setdefault("copyright_year", "an old year")
        return fill(SIGNALS[key][field], local)

    ctx["obs1"] = sig_text(0, "observation") or f"I was looking at {name} and think there's an easy win around {ctx['service']}."
    ctx["cost1"] = sig_text(0, "cost") or "Small gaps like this quietly cost enquiries every week."
    ctx["fix1"] = sig_text(0, "fix") or ctx["starter_offer"]
    ctx["problem1"] = sig_text(0, "problem") or ctx["service"]
    ctx["topic1"] = (SIGNALS[sigs[0][0]].get("topic") if sigs else None) or ctx["service"]
    ctx["obs2"] = sig_text(1, "observation")
    ctx["cost2"] = sig_text(1, "cost")
    ctx["fix2"] = sig_text(1, "fix")
    lc = lambda s: (s[:1].lower() + s[1:]) if s and not s[:2].isupper() else s  # noqa: E731
    ctx["obs1_lc"] = lc(ctx["obs1"])
    ctx["problem1_lc"] = lc(ctx["problem1"])
    ctx["obs2_or_fix"] = ctx["obs2"] or f"the fix I'd suggest, {ctx['fix1']}, usually takes just a day or two."
    ctx["obs2_line"] = f"{ctx['obs2']}\n\n" if ctx["obs2"] else ""
    ctx["cost2_or_cost1"] = ctx["cost2"] or ctx["cost1"]
    ctx["fix2_or_fix1"] = ctx["fix2"] or ctx["fix1"]
    ctx["fix2_or_next"] = ctx["fix2"] or "the next improvement on top of what we built"
    ctx["problems"] = jloads(company.get("problems"), [])
    # Short forms for DMs and call scripts
    ctx["first_or_team"] = first or "there"
    ctx["first_or_owner"] = first or "the owner"
    ctx["business_name"] = p.get("business_name", "")
    ctx["cost1_short"] = ctx["cost1"].split(". ")[0].rstrip(".") + "."
    ctx["proof1_short"] = (proof[0] if proof else f"help {ctx['category_plural']} with {ctx['service']}")
    ctx["online_presence"] = "website" if site else ("Instagram page" if company.get("instagram") else "Google listing")
    from .channels import parse_phone
    ph = parse_phone(contact.get("phone") or company.get("phone"), company.get("country"))
    ctx["phone_line"] = f" · {ph['display']}" if ph else ""
    if extra:
        ctx.update(extra)
    return ctx


def thread_subject(db: DB, contact_id: int) -> str | None:
    row = db.one("""SELECT subject FROM messages WHERE contact_id = ? AND direction = 'out' AND status = 'sent'
                    AND subject IS NOT NULL AND subject != '' ORDER BY id DESC LIMIT 1""", (contact_id,))
    if not row:
        return None
    s = row["subject"]
    while s.lower().startswith("re: "):
        s = s[4:]
    return s


def thread_history(db: DB, contact_id: int, limit: int = 6) -> list[dict]:
    rows = db.all("""SELECT direction, subject, body, purpose, intent FROM messages WHERE contact_id = ?
                     AND status IN ('sent','received') ORDER BY id DESC LIMIT ?""", (contact_id, limit))
    return list(reversed(rows))


class Writer:
    def __init__(self, db: DB, cfg: Config, llm: LLM | None = None):
        self.db, self.cfg = db, cfg
        self.llm = llm or LLM(cfg)

    # ---- AI ----------------------------------------------------------------
    def _ai_draft(self, purpose: str, ctx: dict, contact: dict, company: dict, subject_mode: str,
                  channel: str = "email") -> Draft | None:
        if not self.llm.available:
            return None
        limit = compliance.WORD_LIMITS.get(purpose, 120)
        sigs = jloads(company.get("signals"), {})
        data = {
            "sender": {k: self.cfg.get(f"profile.{k}") for k in
                       ("your_name", "business_name", "service", "service_long", "starter_offer", "quick_win",
                        "proof", "proof_links", "booking_link", "delivery_time")},
            "lead": {"company": company.get("name"), "category": company.get("category"), "city": company.get("city"),
                     "website": display_site(company.get("website")), "contact_name": contact.get("full_name"),
                     "contact_role": contact.get("role")},
            "verified_observations": {k: SIGNALS[k]["problem"] + (f" ({v})" if isinstance(v, str) else "")
                                      for k, v in sigs.items() if k in SIGNALS},
            "auditor_notes": jloads(company.get("problems"), []),
            "site_summary": (company.get("site_summary") or "")[:800],
            "purpose": purpose,
            "purpose_brief": PURPOSE_BRIEFS.get(purpose, ""),
            "word_limit": limit,
            "greeting_line": ctx["greeting"] if channel == "email" else f"Hi {ctx['first_or_team']}",
            "channel": chn.LABELS.get(channel, channel),
            "subject_mode": subject_mode if channel == "email" else "reply",
            "price_allowed": False,
            "thread_so_far": thread_history(self.db, contact["id"]),
        }
        kind_word = "email" if channel == "email" else f"{chn.LABELS.get(channel, channel)} message"
        prompt = f"Write the {kind_word} described below.\n\n" + json.dumps(data, ensure_ascii=False, indent=1, default=str)
        effort = str(self.cfg.get("llm.effort_writing", "medium"))
        for attempt in range(2):
            out = self.llm.json(WRITER_SYSTEM, prompt, DRAFT_SCHEMA, effort=effort)
            if not out or not out.get("body"):
                return None
            subject = (out.get("subject") or "").strip()
            body = out["body"].strip()
            check_subject = subject if subject_mode == "new" else "Re: thread"
            issues = compliance.check_message(self.cfg, check_subject, body, purpose, company.get("name", ""),
                                              channel=channel)
            if not compliance.blocking(issues):
                return Draft(subject=subject, body=body, generator=f"ai:{self.llm.provider}")
            fix = "; ".join(i["detail"] for i in issues if i["level"] == "block")
            prompt += f"\n\nYour previous draft failed these checks: {fix}\nRewrite it so it passes."
            log.info("AI draft failed checks (attempt %d): %s", attempt + 1, fix)
        return None

    # ---- public API -----------------------------------------------------------
    def sequence_draft(self, contact: dict, company: dict, purpose: str, extra: dict | None = None,
                       seed: str | None = None, channel: str = "email") -> Draft:
        ctx = build_context(self.cfg, contact, company, extra)
        if channel == "call":
            key = purpose if purpose in CALL_TEMPLATES else ("call_intro" if purpose in ("intro", "dm_intro", "call_intro")
                                                             else "call_followup")
            return Draft(subject=f"Call {company.get('name', '')}", body=fill(CALL_TEMPLATES[key], ctx), generator="template")
        if channel != "email":
            purpose = DM_EQUIVALENT.get(purpose, purpose)
        variants = SEQUENCE_TEMPLATES.get(purpose) or SEQUENCE_TEMPLATES["bump"]
        t_subject, t_body = pick(variants, seed or f"{contact['id']}:{purpose}")
        prior = thread_subject(self.db, contact["id"]) if channel == "email" else None
        subject_mode = "reply" if (t_subject is None and prior) else "new"
        draft = self._ai_draft(purpose, ctx, contact, company, subject_mode, channel)
        if draft is None:
            subject = fill(t_subject, ctx) if t_subject else ""
            body = fill(t_body, ctx)
            if channel != "email" and body.startswith("Hi ") and "\n\n" in body[:60] and not purpose.startswith("dm_"):
                body = f"Hi {ctx['first_or_team']}! " + body.split("\n\n", 1)[1]  # email greeting -> chat greeting
            draft = Draft(subject=subject, body=body, generator="template")
        if channel != "email":
            draft.subject = ""
        elif subject_mode == "reply":
            draft.subject = f"Re: {prior}"
        elif not draft.subject:
            draft.subject = fill(t_subject or "Quick question about {company}", ctx)
        return draft

    def reply_draft(self, contact: dict, company: dict, intent: str, inbound: dict) -> Draft:
        ctx = build_context(self.cfg, contact, company)
        subj = inbound.get("subject") or thread_subject(self.db, contact["id"]) or f"{company.get('name')}"
        subject = subj if subj.lower().startswith("re:") else f"Re: {subj}"
        if self.llm.available:
            out = self.llm.json(
                WRITER_SYSTEM + "\n\nYou are now the CLOSER: answer the prospect's reply. Move toward a 15-minute call "
                "(booking link provided). Price may be mentioned ONLY if they asked, and only as the given range. "
                "Under 100 words. Return an empty subject.",
                json.dumps({
                    "sender": {k: self.cfg.get(f"profile.{k}") for k in
                               ("your_name", "service", "starter_offer", "price_range", "delivery_time", "proof",
                                "proof_links", "booking_link", "quick_win")},
                    "lead": {"company": company.get("name"), "contact_name": contact.get("full_name")},
                    "their_reply": (inbound.get("body") or "")[:3000],
                    "detected_intent": intent,
                    "suggested_answer": fill(REPLY_TEMPLATES.get(intent, REPLY_TEMPLATES["question"]), ctx),
                    "greeting_line": ctx["greeting_thanks"],
                    "thread_so_far": thread_history(self.db, contact["id"]),
                }, ensure_ascii=False, default=str),
                DRAFT_SCHEMA, effort=str(self.cfg.get("llm.effort_writing", "medium")))
            if out and out.get("body"):
                issues = compliance.check_message(self.cfg, subject, out["body"], None, kind="reply")
                if not compliance.blocking(issues):
                    return Draft(subject=subject, body=out["body"].strip(), generator=f"ai:{self.llm.provider}")
        body = fill(REPLY_TEMPLATES.get(intent, REPLY_TEMPLATES["question"]), ctx)
        return Draft(subject=subject, body=body, generator="template")

    def onboarding_draft(self, contact: dict, company: dict, key: str) -> Draft:
        o = self.cfg.section("onboarding")
        extra = {
            "contract_line": (f"please review and sign here: {o['contract_link']}" if o.get("contract_link")
                              else "I've attached it to this email. Please review, sign and send it back"),
            "payment_line": o.get("payment_link") or "I'll attach the invoice with the bank / UPI details",
            "welcome_doc_line": (f"Here's your welcome document with everything in one place: {o['welcome_doc_link']}"
                                 if o.get("welcome_doc_link") else "Here's a quick summary of how we'll work together:"),
            "kickoff_link": o.get("kickoff_link") or self.cfg.get("profile.booking_link", ""),
            "portal_link": o.get("portal_link") or "(I'll share the link shortly)",
            "access_list": "\n".join(f"- {item}" for item in (o.get("access_items") or [])),
        }
        ctx = build_context(self.cfg, contact, company, extra)
        subject_t, body_t = ONBOARDING_TEMPLATES[key]
        return Draft(subject=fill(subject_t, ctx), body=fill(body_t, ctx), generator="template")


# ---------------------------------------------------------------------------
# Job: write drafts for every contact whose next step is due (or due within draft_ahead_hours)
# ---------------------------------------------------------------------------
def queue_message(db: DB, cfg: Config, contact: dict, company: dict, draft: Draft, kind: str, *,
                  sequence: str | None = None, step: int | None = None, purpose: str | None = None,
                  scheduled_for: str | None = None, in_reply_to: str | None = None, needs_human: bool = False,
                  auto_ok: bool = False, channel: str | None = None, target: str | None = None) -> int:
    channel = channel or ("email" if contact.get("email") else "social")
    issues = compliance.check_message(cfg, draft.subject, draft.body, purpose, company.get("name", ""), kind=kind,
                                      channel=channel)
    status = "approved" if (auto_ok and channel == "email" and not compliance.blocking(issues) and not needs_human) else "pending"
    to = target or (contact.get("email") if channel == "email" else None)
    return db.insert("messages", {
        "contact_id": contact["id"], "company_id": company.get("id"), "direction": "out",
        "channel": channel, "kind": kind, "sequence": sequence, "step": step,
        "purpose": purpose, "subject": draft.subject, "body": draft.body, "status": status,
        "check_issues": json.dumps(issues), "in_reply_to": in_reply_to, "to_addr": to,
        "action_url": chn.action_url(channel, to, draft.body) if channel not in ("email", "social") and to else None,
        "generator": draft.generator, "needs_human": 1 if needs_human else 0,
        "scheduled_for": scheduled_for or iso(utcnow()),
    })


def tasks_created_today(db: DB, cfg: Config) -> int:
    from .sender import day_bounds_utc
    a, b = day_bounds_utc(cfg)
    return int(db.scalar("""SELECT COUNT(*) FROM messages WHERE direction='out' AND kind='sequence'
                            AND channel NOT IN ('email') AND created_at >= ? AND created_at < ?""", (a, b)) or 0)


def write_due(db: DB, cfg: Config, writer: Writer | None = None, limit: int = 25) -> int:
    """Write the next message for every contact whose step is due (or due soon). Picks the channel per lead:
    email is queued for sending; WhatsApp / Instagram / LinkedIn / X / Facebook / calls become To-do tasks."""
    writer = writer or Writer(db, cfg)
    ahead = float(cfg.get("approval.draft_ahead_hours", 24))
    horizon = iso(utcnow() + timedelta(hours=ahead))
    autopilot = bool(db.kv_get("autopilot_cold", cfg.get("approval.autopilot_cold", False)))
    ch_cfg = cfg.section("channels")
    task_cap = int(ch_cfg.get("tasks_per_day", 25))
    min_task_score = float(ch_cfg.get("min_score_for_tasks", 40))
    rows = db.all("""SELECT ct.* FROM contacts ct LEFT JOIN companies co ON co.id = ct.company_id
                     WHERE ct.sequence IS NOT NULL AND ct.next_action_at IS NOT NULL AND ct.next_action_at <= ?
                     ORDER BY co.priority DESC, co.score DESC, ct.next_action_at LIMIT ?""", (horizon, limit * 3))
    written = 0
    tasks_today = tasks_created_today(db, cfg)
    for c in rows:
        if written >= limit:
            break
        info = step_info(cfg, c["sequence"], c["step"])
        if not info:
            continue  # finished track, handled by pipeline.process_timers
        exists = db.scalar("""SELECT 1 FROM messages WHERE contact_id=? AND kind='sequence' AND sequence=? AND step=?
                              AND status IN ('pending','approved','sent')""", (c["id"], c["sequence"], c["step"]))
        if exists:
            continue
        company = db.company(c["company_id"]) or {}
        avail = {k: v for k, v in chn.available(company, c, cfg).items() if not is_suppressed(db, v)}
        channel = chn.pick_channel(info.get("channel", "email"), avail, cfg)
        if channel is None:
            skip_step(db, cfg, c, f"no way to reach them by {info.get('channel', 'email')}")
            continue
        if channel != "email":
            extra_touch = bool(c.get("email"))  # email leads get calls/DMs only when they're worth your time
            if extra_touch and float(company.get("score") or 0) < min_task_score and not company.get("priority"):
                skip_step(db, cfg, c, f"call/DM tasks only for leads scoring {min_task_score:.0f}+")
                continue
            if tasks_today >= task_cap:
                postpone(db, c["id"], 20, "daily task limit reached")
                continue
        purpose = info.get("purpose", "bump")
        if c["sequence"] in ("cold", "cold_social") and c["step"] == 0 and (c.get("notes") or "").startswith("referral:"):
            purpose = "referral_intro"
        extra = {"referrer": (c.get("notes") or "").split(":", 1)[-1].strip()} if purpose == "referral_intro" else None
        draft = writer.sequence_draft(c, company, purpose, extra, channel=channel)
        queue_message(db, cfg, c, company, draft, "sequence", sequence=c["sequence"], step=c["step"],
                      purpose=purpose, scheduled_for=c["next_action_at"], channel=channel, target=avail.get(channel),
                      auto_ok=autopilot and channel == "email")
        written += 1
        if channel != "email":
            tasks_today += 1
        db.log("write", f"{'drafted' if channel == 'email' else 'task:'} {chn.LABELS.get(channel, channel)} · "
               f"{c['sequence']} step {c['step'] + 1} ({purpose}) for {c['email'] or company.get('name')} [{draft.generator}]",
               contact_id=c["id"], company_id=c["company_id"])
    return written


def regenerate(db: DB, cfg: Config, message_id: int, writer: Writer | None = None) -> int | None:
    """Throw away a draft and write a fresh one for the same step."""
    m = db.message(message_id)
    if not m or m["status"] not in ("pending", "approved", "rejected"):
        return None
    db.update("messages", message_id, {"status": "rejected"})
    contact, company = db.contact(m["contact_id"]), db.company(m["company_id"]) or {}
    if not contact or m["kind"] != "sequence":
        return None
    writer = writer or Writer(db, cfg)
    # A different seed gives a different template variant (and a fresh AI draft).
    draft = writer.sequence_draft(contact, company, m["purpose"], seed=f"{contact['id']}:{m['purpose']}:{message_id}",
                                  channel=m.get("channel") or "email")
    if parse_iso(m["scheduled_for"]) is None:
        m["scheduled_for"] = iso(utcnow())
    return queue_message(db, cfg, contact, company, draft, "sequence", sequence=m["sequence"], step=m["step"],
                         purpose=m["purpose"], scheduled_for=m["scheduled_for"], channel=m.get("channel"),
                         target=m.get("to_addr"))
