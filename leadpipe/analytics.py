"""The Analyst: what's working. Tracks the numbers from the 30-day challenge: sent, bounce rate, reply rate,
positive replies, meetings, wins, broken down by step, signal and category."""
from __future__ import annotations

from datetime import timedelta

from .classifier import HOT
from .config import Config
from .db import DB, iso, jloads, utcnow
from .pipeline import STAGES
from .sender import day_bounds_utc, sent_today, todays_cap

POSITIVE = tuple(HOT)


def pct(a: float, b: float) -> float:
    return round(100.0 * a / b, 1) if b else 0.0


def overview(db: DB, cfg: Config, days: int = 30) -> dict:
    since = iso(utcnow() - timedelta(days=days))
    q = lambda sql, *p: db.scalar(sql, p) or 0  # noqa: E731
    sent = q("SELECT COUNT(*) FROM messages WHERE direction='out' AND status='sent' AND channel='email' AND sent_at >= ?", since)
    contacted = q("SELECT COUNT(DISTINCT contact_id) FROM messages WHERE direction='out' AND status='sent' AND kind='sequence' AND sent_at >= ?", since)
    # Only count answers from people we actually emailed in this window, so rates can't exceed 100%.
    reached = ("AND contact_id IN (SELECT contact_id FROM messages WHERE direction='out' AND status='sent' "
               "AND kind='sequence' AND sent_at >= ?)")
    replied = q(f"""SELECT COUNT(DISTINCT contact_id) FROM messages WHERE direction='in' AND intent NOT IN ('bounce','auto_reply')
                   AND received_at >= ? {reached}""", since, since)
    positive = q(f"""SELECT COUNT(DISTINCT contact_id) FROM messages WHERE direction='in' AND received_at >= ?
                     AND intent IN ({','.join('?' for _ in POSITIVE)}) {reached}""", since, *POSITIVE, since)
    bounced = q(f"SELECT COUNT(DISTINCT contact_id) FROM messages WHERE direction='in' AND intent='bounce' AND received_at >= ? {reached}", since, since)
    unsub = q(f"SELECT COUNT(DISTINCT contact_id) FROM messages WHERE direction='in' AND intent='unsubscribe' AND received_at >= ? {reached}", since, since)
    meetings = q("SELECT COUNT(*) FROM events WHERE kind='stage' AND message LIKE '%-> meeting%' AND ts >= ?", since)
    won = q("SELECT COUNT(*) FROM deals WHERE won_at >= ?", since)
    revenue = q("SELECT COALESCE(SUM(value),0) FROM deals WHERE won_at >= ?", since)
    a, b = day_bounds_utc(cfg)
    return {
        "days": days, "sent": sent, "contacted": contacted, "replied": replied, "positive": positive,
        "bounced": bounced, "unsubscribed": unsub, "meetings": meetings, "won": won, "revenue": revenue,
        "reply_rate": pct(replied, contacted), "positive_rate": pct(positive, contacted),
        "bounce_rate": pct(bounced, contacted), "meeting_rate": pct(meetings, contacted),
        "today": {
            "sent": sent_today(db, cfg), "cap": todays_cap(db, cfg),
            "first_touch": sent_today(db, cfg, first_touch_only=True),
            "found": q("SELECT COUNT(*) FROM companies WHERE created_at >= ? AND created_at < ?", a, b),
            "replies": q("SELECT COUNT(*) FROM messages WHERE direction='in' AND received_at >= ? AND received_at < ?", a, b),
        },
        "waiting_on_you": {
            "outbox": q("SELECT COUNT(*) FROM messages WHERE direction='out' AND status='pending' AND channel='email'"),
            "replies": q("SELECT COUNT(*) FROM messages WHERE direction='out' AND status='pending' AND kind='reply'"),
            "manual": q("SELECT COUNT(*) FROM messages WHERE direction='out' AND status IN ('pending','approved') AND channel='manual' AND scheduled_for <= ?", iso(utcnow() + timedelta(days=1))),
            "flagged": q("SELECT COUNT(*) FROM messages WHERE direction='in' AND needs_human=1 AND handled IN (0,1) AND received_at >= ?", iso(utcnow() - timedelta(days=7))),
        },
    }


def funnel(db: DB) -> list[dict]:
    rows = {r["stage"]: r["n"] for r in db.all("SELECT stage, COUNT(*) n FROM contacts GROUP BY stage")}
    return [{"stage": s, "label": label, "count": rows.get(s, 0)} for s, label in STAGES.items()]


def companies_by_status(db: DB) -> dict:
    return {r["status"]: r["n"] for r in db.all("SELECT status, COUNT(*) n FROM companies GROUP BY status")}


def by_step(db: DB) -> list[dict]:
    """Reply rate per follow-up step: which email in the sequence actually gets answered."""
    rows = db.all("""SELECT m.sequence, m.step, m.purpose, COUNT(*) sent,
        SUM(CASE WHEN EXISTS (SELECT 1 FROM messages r WHERE r.contact_id = m.contact_id AND r.direction='in'
             AND r.intent NOT IN ('bounce','auto_reply') AND r.received_at >= m.sent_at
             AND NOT EXISTS (SELECT 1 FROM messages m2 WHERE m2.contact_id = m.contact_id AND m2.direction='out'
                  AND m2.status='sent' AND m2.kind='sequence' AND m2.sent_at > m.sent_at AND m2.sent_at <= r.received_at))
            THEN 1 ELSE 0 END) replies
        FROM messages m WHERE m.direction='out' AND m.status='sent' AND m.kind='sequence'
        GROUP BY m.sequence, m.step, m.purpose ORDER BY m.sequence, m.step""")
    for r in rows:
        r["reply_rate"] = pct(r["replies"] or 0, r["sent"])
    return rows


def by_signal(db: DB) -> list[dict]:
    """Which buying signals lead to replies: tells you which problems to target."""
    stats: dict[str, dict] = {}
    rows = db.all("""SELECT co.signals, ct.id cid,
        EXISTS (SELECT 1 FROM messages r WHERE r.contact_id = ct.id AND r.direction='in'
                AND r.intent NOT IN ('bounce','auto_reply','unsubscribe','not_interested')) positive
        FROM contacts ct JOIN companies co ON co.id = ct.company_id WHERE ct.last_outbound_at IS NOT NULL""")
    for r in rows:
        for sig in jloads(r["signals"], {}):
            s = stats.setdefault(sig, {"signal": sig, "contacted": 0, "positive": 0})
            s["contacted"] += 1
            s["positive"] += int(r["positive"] or 0)
    out = sorted(stats.values(), key=lambda s: -s["contacted"])
    for s in out:
        s["positive_rate"] = pct(s["positive"], s["contacted"])
    return out


def by_category(db: DB) -> list[dict]:
    rows = db.all("""SELECT COALESCE(co.category,'?') category, COUNT(DISTINCT ct.id) contacted,
        COUNT(DISTINCT CASE WHEN r.id IS NOT NULL THEN ct.id END) positive
        FROM contacts ct JOIN companies co ON co.id = ct.company_id
        LEFT JOIN messages r ON r.contact_id = ct.id AND r.direction='in' AND r.intent NOT IN ('bounce','auto_reply','unsubscribe','not_interested')
        WHERE ct.last_outbound_at IS NOT NULL GROUP BY co.category ORDER BY contacted DESC""")
    for r in rows:
        r["positive_rate"] = pct(r["positive"], r["contacted"])
    return rows


def digest_text(db: DB, cfg: Config) -> str:
    o = overview(db, cfg, days=7)
    t, w = o["today"], o["waiting_on_you"]
    hot = db.all("""SELECT co.name, m.intent FROM messages m JOIN companies co ON co.id = m.company_id
                    WHERE m.direction='in' AND m.received_at >= ? AND m.intent NOT IN ('bounce','auto_reply','unsubscribe','not_interested')
                    ORDER BY m.id DESC LIMIT 10""", (iso(utcnow() - timedelta(days=1)),))
    lines = [
        f"Today: {t['sent']}/{t['cap']} emails sent ({t['first_touch']} new leads), {t['replies']} replies, {t['found']} businesses found.",
        f"Waiting on you: {w['outbox']} emails to approve, {w['replies']} replies to approve, {w['manual']} manual DMs/calls.",
        f"Last 7 days: {o['contacted']} contacted, reply rate {o['reply_rate']}%, positive {o['positive_rate']}%, "
        f"bounce {o['bounce_rate']}%, {o['meetings']} calls booked, {o['won']} deals won.",
    ]
    if hot:
        lines.append("Hot in the last 24h: " + "; ".join(f"{h['name']} ({h['intent']})" for h in hot))
    if o["bounce_rate"] > 5 and o["contacted"] >= 20:
        lines.append("WARNING: bounce rate above 5%. Check email verification and pause sending if it keeps rising.")
    return "\n".join(lines)
