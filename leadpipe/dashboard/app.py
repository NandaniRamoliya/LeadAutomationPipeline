"""Web dashboard: 5 simple pages.

    Home      is it running? what's happening? what's next?
    To-do     everything that needs you, one list, one button each
    Leads     every business: who, where, why they're a good lead, what happens next
    Clients   people who said yes: onboarding progress
    Settings  setup form, where to reach, channels, mailboxes, run-in-background, import, do-not-contact

Private by default (127.0.0.1). If you expose it (0.0.0.0 / a domain), DASHBOARD_PASSWORD is required.
Public on purpose: /u/<token> (unsubscribe), /webhooks/booking (calendar bookings), /health.
"""
from __future__ import annotations

import hmac
import json
import logging
import secrets
import threading
import time
from datetime import timedelta

from flask import Flask, Response, abort, flash, redirect, render_template, request, session, url_for

from .. import channels as chn
from .. import compliance, onboarding, tasks
from ..analytics import by_category, by_signal, by_step, overview
from ..classifier import INTENTS
from ..config import Config, env_file_values, save_env, save_settings
from ..db import DB, iso, jloads, now_iso, parse_iso, utcnow
from ..engine import engine_status
from ..pipeline import STAGES, set_stage, step_info, steps_for, suppress
from ..sender import verify_unsub_token
from ..templates import SIGNALS, Draft
from ..writer import Writer, queue_message, regenerate

log = logging.getLogger("leadpipe.dashboard")
PUBLIC_ENDPOINTS = {"unsubscribe", "booking_webhook", "static", "health"}

# Plain-language groups for the Leads page
VIEWS = {
    "all": ("All", None),
    "new": ("Not contacted yet", ("new",)),
    "reaching": ("Reaching out", ("cold",)),
    "talking": ("Talking", ("interested", "meeting", "proposal")),
    "won": ("Clients", ("won", "client")),
    "later": ("Later", ("nurture", "paused")),
    "closed": ("Closed", ("lost", "unsubscribed", "bounced")),
}
PLAIN_STAGE = {
    "new": "Not contacted yet", "cold": "Reaching out", "interested": "Interested", "meeting": "Call booked",
    "proposal": "Deciding", "nurture": "Later (check back)", "won": "Won: onboarding", "client": "Client",
    "lost": "Closed", "unsubscribed": "Opted out", "bounced": "Email bounced", "paused": "Paused",
}
STAGE_PILL = {"interested": "ok", "meeting": "ok", "proposal": "ok", "won": "acc", "client": "acc", "lost": "",
              "unsubscribed": "bad", "bounced": "bad", "cold": "warn", "nurture": "", "paused": "", "new": ""}


def next_step_text(cfg: Config, contact: dict) -> str:
    """'Email 2 of 5 · in 3 days' style description of what happens next."""
    seq = contact.get("sequence")
    if contact.get("stage") == "meeting":
        return "Proposal follow-ups start after the call"
    if not seq:
        return {"won": "Onboarding steps (Clients page)", "client": "Client care check-ins"}.get(contact.get("stage"), "Nothing scheduled")
    info = step_info(cfg, seq, int(contact.get("step") or 0))
    when = parse_iso(contact.get("next_action_at"))
    rel = _rel(when)
    if not info:
        return f"Closes {rel} if there's no reply"
    total = len(steps_for(cfg, seq))
    ch = info.get("channel", "email")
    word = {"email": "Email", "call": "Call", "dm": "Message"}.get(ch, chn.LABELS.get(ch, ch))
    return f"{word} {int(contact.get('step') or 0) + 1} of {total} · {rel}"


def _rel(dt) -> str:
    if not dt:
        return ""
    secs = (dt - utcnow()).total_seconds()
    if abs(secs) < 3600:
        return "now" if secs <= 0 else "within the hour"
    days = secs / 86400
    if secs < 0:
        return "now (due)"
    if days < 1:
        return f"in {int(secs // 3600)} h"
    return f"in {int(round(days))} day{'s' if round(days) != 1 else ''}"


def create_app(cfg: Config, db: DB, engine=None) -> Flask:
    app = Flask(__name__)
    app.secret_key = cfg.env("LEADPIPE_SECRET") or db.kv_get("flask_secret") or secrets.token_hex(32)
    db.kv_set("flask_secret", app.secret_key)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", MAX_CONTENT_LENGTH=20 * 1024 * 1024)
    holder: dict = {"service": (0.0, None)}

    def writer() -> Writer:
        if "w" not in holder:
            holder["w"] = engine.writer if engine is not None else Writer(db, cfg)
        return holder["w"]

    def service_status() -> dict:
        ts, st = holder["service"]
        if st is None or time.time() - ts > 60:
            from .. import service
            try:
                st = service.status(cfg)
            except Exception as exc:  # pragma: no cover
                st = {"installed": False, "running": False, "error": str(exc)}
            holder["service"] = (time.time(), st)
        return st

    # ---- security --------------------------------------------------------------
    @app.before_request
    def _auth():
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        password = cfg.env("DASHBOARD_PASSWORD")
        if password:
            a = request.authorization
            if not a or not hmac.compare_digest(a.password or "", password):
                return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Orbit"'})
        if request.method == "POST":
            token = request.form.get("_csrf") or request.headers.get("X-CSRF")
            if not token or not hmac.compare_digest(token, session.get("csrf", "")):
                abort(400, "Form expired: reload the page and try again.")
        return None

    @app.context_processor
    def _globals():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {"csrf": session["csrf"], "status": engine_status(db, cfg), "STAGES": STAGES, "INTENTS": INTENTS,
                "jloads": jloads, "profile": cfg.section("profile"), "todo_count": tasks.counts(db, cfg)["total"],
                "PLAIN_STAGE": PLAIN_STAGE, "STAGE_PILL": STAGE_PILL, "LABELS": chn.LABELS, "ICONS": chn.ICONS,
                "setup_needed": setup_needed(cfg)}

    @app.template_filter("ago")
    def _ago(value):
        dt = parse_iso(value)
        if not dt:
            return "never"
        secs = (utcnow() - dt).total_seconds()
        future = secs < 0
        secs = abs(secs)
        for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
            if secs >= n:
                s = f"{int(secs // n)}{unit}"
                return f"in {s}" if future else f"{s} ago"
        return "now"

    @app.template_filter("short")
    def _short(value, n=90):
        value = (value or "").replace("\n", " ")
        return value if len(value) <= n else value[: n - 1] + "…"

    @app.template_filter("evidence")
    def _evidence(value):
        if isinstance(value, dict):
            return ", ".join(str(v) for v in value.values())
        return value

    # =========================================================================
    # Home
    # =========================================================================
    @app.get("/")
    def home():
        o = overview(db, cfg, 7)
        from ..discovery import current_area, seed_areas
        try:
            seed_areas(db, cfg)
        except Exception:
            pass
        area = db.one("SELECT * FROM areas WHERE status='active' ORDER BY position LIMIT 1") or current_area(db)
        upcoming = db.all("SELECT name FROM areas WHERE status='pending' ORDER BY position, id LIMIT 4")
        done_areas = db.scalar("SELECT COUNT(*) FROM areas WHERE status='done'") or 0
        counts = {k: db.scalar("SELECT COUNT(*) FROM contacts WHERE stage IN (%s)" % ",".join("?" for _ in v[1]), v[1]) or 0
                  for k, v in VIEWS.items() if v[1]}
        events = db.all("SELECT * FROM events WHERE level != 'debug' ORDER BY id DESC LIMIT 12")
        return render_template("home.html", o=o, area=area, upcoming=upcoming, done_areas=done_areas, counts=counts,
                               events=events, todo=tasks.counts(db, cfg), service=service_status(),
                               companies=db.scalar("SELECT COUNT(*) FROM companies") or 0)

    @app.get("/health")
    def health():
        st = engine_status(db, cfg)
        return {"ok": True, "engine_alive": st["alive"], "heartbeat": st["heartbeat"]}

    @app.post("/engine/<action>")
    def engine_action(action: str):
        if action == "pause":
            db.kv_set("engine_paused", True)
            db.kv_set("sending_paused", True)
            flash("Paused. Nothing will be searched, written or sent until you press Resume.", "ok")
        elif action == "resume":
            db.kv_set("engine_paused", False)
            db.kv_set("sending_paused", False)
            flash("Running again.", "ok")
        elif action in ("install", "uninstall"):
            from .. import service
            msg = service.install(cfg) if action == "install" else service.uninstall(cfg)
            holder["service"] = (0.0, None)
            flash(msg, "ok")
        db.log("settings", f"engine {action} (dashboard)")
        return redirect(request.referrer or url_for("home"))

    # =========================================================================
    # To-do
    # =========================================================================
    @app.get("/todo")
    def todo():
        t = tasks.todo(db, cfg)
        return render_template("todo.html", t=t, sections=tasks.SECTIONS, outcomes=tasks.CALL_OUTCOMES)

    def _save_edits(mid: int) -> dict | None:
        m = db.message(mid)
        if not m:
            return None
        subject = request.form.get("subject", m["subject"] or "")
        body = request.form.get("body", m["body"] or "")
        company = db.company(m["company_id"]) or {}
        issues = compliance.check_message(cfg, subject, body, m["purpose"], company.get("name", ""), kind=m["kind"],
                                          channel=m.get("channel") or "email")
        vals = {"subject": subject, "body": body, "check_issues": json.dumps(issues)}
        if m.get("channel") not in ("email", "call", None) and m.get("to_addr"):
            vals["action_url"] = chn.action_url(m["channel"], m["to_addr"], body)
        db.update("messages", mid, vals)
        return db.message(mid)

    @app.post("/todo/<int:mid>/<action>")
    def todo_action(mid: int, action: str):
        m = _save_edits(mid) if action in ("send", "save", "done") else db.message(mid)
        if not m:
            abort(404)
        issues = jloads(m["check_issues"], [])
        if action == "send":
            if compliance.blocking(issues):
                flash("Not sent: " + "; ".join(i["detail"] for i in issues if i["level"] == "block"), "error")
            else:
                db.update("messages", mid, {"status": "approved", "needs_human": 0})
                flash("Approved. It goes out automatically at the right time (their office hours).", "ok")
        elif action == "save":
            flash("Saved.", "ok")
        elif action == "done":
            tasks.done(db, cfg, mid, request.form.get("body"))
            flash("Done! The next follow-up is scheduled.", "ok")
        elif action == "rewrite":
            new = regenerate(db, cfg, mid, writer())
            flash("A fresh version was written." if new else "Rewritten.", "ok")
        elif action == "skip":
            tasks.skip(db, cfg, mid)
            flash("Skipped. The next follow-up is scheduled.", "ok")
        elif action == "discard":
            db.update("messages", mid, {"status": "cancelled", "error": "discarded by you"})
            flash("Discarded.", "ok")
        elif action == "stop":
            set_stage(db, cfg, m["contact_id"], "paused", "stopped by you")
            flash("Stopped: no more messages to them.", "ok")
        elif action == "call":
            flash(tasks.call_outcome(db, cfg, mid, request.form.get("outcome", "no_answer"),
                                     request.form.get("notes", ""), writer()), "ok")
        return redirect(request.referrer or url_for("todo"))

    @app.post("/todo/approve-all")
    def approve_all():
        n = 0
        for r in db.all("SELECT id, check_issues FROM messages WHERE direction='out' AND status='pending' AND channel='email' AND kind='sequence'"):
            if not compliance.blocking(jloads(r["check_issues"], [])):
                db.update("messages", r["id"], {"status": "approved"})
                n += 1
        flash(f"Approved {n} emails that passed every check. Replies to people still wait for you one by one.", "ok")
        return redirect(url_for("todo"))

    @app.post("/todo/client/<int:cid>")
    def todo_client(cid: int):
        try:
            onboarding.set_step(db, cfg, cid, request.form["key"], True)
            flash("Done. The next onboarding email is ready.", "ok")
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
        return redirect(request.referrer or url_for("todo"))

    # =========================================================================
    # Leads
    # =========================================================================
    @app.get("/leads")
    def leads():
        view = request.args.get("view", "all")
        q = request.args.get("q", "").strip()
        area_id = request.args.get("area", "")
        has = request.args.getlist("has")
        hasnot = request.args.getlist("not")
        min_score = request.args.get("min", "")
        prio = request.args.get("priority") == "1"
        sql = """SELECT ct.*, co.name company, co.score, co.priority, co.city, co.category, co.signals, co.website,
                        co.phone company_phone, co.instagram, co.linkedin, co.twitter, co.facebook, co.whatsapp, co.country,
                        co.status company_status, co.status_reason
                 FROM contacts ct JOIN companies co ON co.id = ct.company_id WHERE 1=1"""
        params: list = []
        stages = VIEWS.get(view, VIEWS["all"])[1]
        if stages:
            sql += " AND ct.stage IN (%s)" % ",".join("?" for _ in stages)
            params += list(stages)
        if q:
            sql += " AND (co.name LIKE ? OR co.website LIKE ? OR co.category LIKE ? OR ct.email LIKE ? OR co.city LIKE ?)"
            params += [f"%{q}%"] * 5
        if area_id:
            sql += " AND co.area_id = ?"
            params.append(area_id)
        if min_score:
            sql += " AND co.score >= ?"
            params.append(float(min_score))
        if prio:
            sql += " AND co.priority = 1"
        for sig in has:
            sql += " AND co.signals LIKE ?"
            params.append(f'%"{sig}"%')
        for sig in hasnot:
            sql += " AND co.signals NOT LIKE ?"
            params.append(f'%"{sig}"%')
        rows = db.all(sql + " ORDER BY co.priority DESC, co.score DESC, ct.id DESC LIMIT 400", params)
        for r in rows:
            r["next"] = next_step_text(cfg, r)
            r["chans"] = list(chn.available({**r, "phone": r.get("company_phone")}, r, cfg).keys())
        counts = {k: (db.scalar("SELECT COUNT(*) FROM contacts WHERE stage IN (%s)" % ",".join("?" for _ in v[1]), v[1])
                      if v[1] else db.scalar("SELECT COUNT(*) FROM contacts")) or 0 for k, v in VIEWS.items()}
        areas = db.all("SELECT id, name FROM areas WHERE found > 0 ORDER BY position")
        unqualified = db.scalar("SELECT COUNT(*) FROM companies WHERE status IN ('new','disqualified','error')") or 0
        return render_template("leads.html", rows=rows, view=view, views=VIEWS, counts=counts, q=q, areas=areas,
                               area_id=area_id, has=has, hasnot=hasnot, min_score=min_score, prio=prio,
                               SIGNALS=SIGNALS, unqualified=unqualified)

    @app.get("/leads/researching")
    def researching():
        rows = db.all("""SELECT * FROM companies WHERE status IN ('new','disqualified','error')
                         ORDER BY CASE status WHEN 'new' THEN 0 ELSE 1 END, id DESC LIMIT 300""")
        return render_template("researching.html", rows=rows)

    @app.get("/company/<int:cid>")
    def company(cid: int):
        db.company(cid) or abort(404)
        cts = db.all("SELECT id FROM contacts WHERE company_id = ? ORDER BY id", (cid,))
        if cts:
            return redirect(url_for("lead", cid=cts[0]["id"]))
        co = db.company(cid)
        return render_template("company.html", co=co, SIGNALS=SIGNALS)

    @app.get("/lead/<int:cid>")
    @app.get("/contact/<int:cid>")
    def lead(cid: int):
        ct = db.contact(cid) or abort(404)
        co = db.company(ct["company_id"]) or {}
        msgs = db.all("SELECT * FROM messages WHERE contact_id = ? AND status NOT IN ('cancelled','rejected') ORDER BY id", (cid,))
        deal = db.one("SELECT * FROM deals WHERE contact_id = ?", (cid,))
        events = db.all("SELECT * FROM events WHERE contact_id = ? OR company_id = ? ORDER BY id DESC LIMIT 30", (cid, ct["company_id"]))
        colleagues = db.all("SELECT id, full_name, email, stage FROM contacts WHERE company_id = ? AND id != ?", (ct["company_id"], cid))
        avail = chn.available(co, ct, cfg)
        links = {ch: chn.action_url(ch, v) for ch, v in avail.items()}
        return render_template("lead.html", ct=ct, co=co, msgs=msgs, deal=deal, events=events, colleagues=colleagues,
                               SIGNALS=SIGNALS, CHECKLIST=onboarding.CHECKLIST, MANUAL_KEYS=onboarding.MANUAL_KEYS,
                               links=links, next_text=next_step_text(cfg, ct), area=db.one("SELECT * FROM areas WHERE id=?", (co.get("area_id"),)))

    @app.post("/lead/<int:cid>/<action>")
    def lead_action(cid: int, action: str):
        ct = db.contact(cid) or abort(404)
        co = db.company(ct["company_id"]) or {}
        try:
            if action == "stage":
                st = request.form["stage"]
                if st == "won":
                    onboarding.start(db, cfg, cid, _float(request.form.get("value")))
                    flash("Deal WON! Onboarding started: the contract message is on your To-do.", "ok")
                else:
                    set_stage(db, cfg, cid, st, request.form.get("reason") or "set by you")
                    flash(f"Moved to '{PLAIN_STAGE.get(st, st)}'. Its follow-ups are scheduled.", "ok")
            elif action == "won":
                onboarding.start(db, cfg, cid, _float(request.form.get("value")), request.form.get("currency") or None)
                flash("Deal WON! Onboarding started: the contract message is on your To-do.", "ok")
            elif action == "note":
                note = request.form.get("note", "").strip()
                db.update("contacts", cid, {"notes": ((ct.get("notes") or "") + "\n" + note).strip()})
                db.log("note", note, contact_id=cid)
            elif action == "log-reply":
                r = tasks.log_reply(db, cfg, cid, request.form.get("text", "").strip(), request.form.get("intent") or None,
                                    request.form.get("channel") or "whatsapp", writer())
                flash(f"Logged as '{INTENTS.get(r['intent'], r['intent'])}'. The next step is on your To-do.", "ok")
            elif action == "compose":
                ch = request.form.get("channel") or ("email" if ct.get("email") else "whatsapp")
                avail = chn.available(co, ct, cfg)
                draft = Draft(subject=request.form.get("subject", "") if ch == "email" else "",
                              body=request.form.get("body", ""), generator="you")
                queue_message(db, cfg, ct, co, draft, "reply", channel=ch, target=avail.get(ch))
                flash("Added to your To-do.", "ok")
            elif action == "priority":
                db.update("companies", co["id"], {"priority": 0 if co.get("priority") else 1})
                flash("High-value flag updated.", "ok")
            elif action == "suppress":
                if ct.get("email"):
                    suppress(db, ct["email"], "marked do-not-contact by you")
                for v in chn.available(co, ct, None).values():
                    suppress(db, v, "marked do-not-contact by you")
                set_stage(db, cfg, cid, "unsubscribed", "do-not-contact (you)")
                flash("They will never be contacted again.", "ok")
            elif action == "onboard":
                onboarding.set_step(db, cfg, cid, request.form["key"], request.form.get("done") == "1")
                flash("Onboarding updated.", "ok")
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("lead", cid=cid))

    # =========================================================================
    # Clients
    # =========================================================================
    @app.get("/clients")
    def clients():
        deals = db.all("""SELECT d.*, co.name company, ct.full_name, ct.email, ct.stage FROM deals d
                          JOIN contacts ct ON ct.id = d.contact_id LEFT JOIN companies co ON co.id = d.company_id
                          ORDER BY d.won_at DESC""")
        for d in deals:
            d["done"], d["total"] = onboarding.progress(d)
        return render_template("clients.html", deals=deals, CHECKLIST=onboarding.CHECKLIST, MANUAL_KEYS=onboarding.MANUAL_KEYS)

    # =========================================================================
    # Insights
    # =========================================================================
    @app.get("/insights")
    @app.get("/analytics")
    def insights():
        days = int(request.args.get("days", 30))
        ch_rows = db.all("""SELECT channel, COUNT(*) n FROM messages WHERE direction='out' AND status='sent'
                            AND sent_at >= ? GROUP BY channel ORDER BY n DESC""", (iso(utcnow() - timedelta(days=days)),))
        return render_template("insights.html", o=overview(db, cfg, days), steps=by_step(db), signals=by_signal(db),
                               categories=by_category(db), days=days, SIGNALS=SIGNALS, ch_rows=ch_rows)

    # =========================================================================
    # Settings
    # =========================================================================
    @app.get("/settings")
    def settings():
        from ..discovery import seed_areas
        from ..mailboxes import mailboxes
        try:
            seed_areas(db, cfg)
        except Exception:
            pass
        areas = db.all("""SELECT * FROM areas ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'pending' THEN 1
                          WHEN 'done' THEN 2 ELSE 3 END, position, id LIMIT 300""")
        envv = env_file_values(cfg)
        from ..presets import PRESETS, preset
        from urllib.parse import urlparse
        site_host = (urlparse(cfg.get("profile.website") or "").hostname or "").replace("www.", "")
        biz = "".join(ch for ch in (cfg.get("profile.business_name") or "yourname").lower() if ch.isalnum()) or "yourname"
        suggest = f"get{site_host}" if site_host and site_host.count(".") == 1 else f"get{biz}.com"
        no_site_win = cfg.get("profile.quick_win_no_site") or (preset(cfg) or PRESETS["website"])["quick_win_no_site"]
        return render_template("settings.html", cfg=cfg, areas=areas, env=envv, mailboxes=mailboxes(cfg),
                               PRESETS=PRESETS, suggest_domain=suggest, no_site_win=no_site_win,
                               suppression=db.all("SELECT * FROM suppression ORDER BY created_at DESC LIMIT 200"),
                               service=service_status(), SIGNALS=SIGNALS, ALL_CHANNELS=chn.ALL_CHANNELS,
                               tab=request.args.get("tab", "setup"))

    @app.post("/settings/setup")
    def settings_setup():
        from ..geo import country_code_for
        from ..presets import PRESETS, weights_for
        f = request.form
        warnings: list[str] = []
        old_type = str(cfg.get("profile.service_type") or "website")
        old_signals = dict(cfg.section("signals"))
        old_types = list(cfg.get("reach.business_types") or [])
        lst = lambda k: [x.strip() for x in f.get(k, "").replace("\n", ",").split(",") if x.strip()]  # noqa: E731
        changes = {
            "profile.your_name": f.get("your_name", "").strip(),
            "profile.business_name": f.get("business_name", "").strip(),
            "profile.website": f.get("website", "").strip(),
            "profile.service": f.get("service", "").strip(),
            "profile.service_long": f.get("service_long", "").strip(),
            "profile.starter_offer": f.get("starter_offer", "").strip(),
            "profile.price_range": f.get("price_range", "").strip(),
            "profile.delivery_time": f.get("delivery_time", "").strip(),
            "profile.quick_win": f.get("quick_win", "").strip(),
            "profile.booking_link": f.get("booking_link", "").strip(),
            "profile.physical_address": f.get("physical_address", "").strip(),
            "profile.proof": [x.strip() for x in f.get("proof", "").splitlines() if x.strip()],
            "profile.proof_links": lst("proof_links"),
            "profile.signature": f.get("signature", "").strip() + "\n",
            "reach.business_types": [x.lower().replace(" ", "_") if "=" not in x else x for x in lst("business_types")],
            "reach.start_area": f.get("start_area", "").strip(),
            "reach.next_areas": [x.strip() for x in f.get("next_areas", "").splitlines() if x.strip()],
            "reach.world_rollout": f.get("world_rollout") == "1",
            "reach.countries": _codes(lst("countries"), warnings, "Then these countries"),
            "reach.skip_countries": _codes(lst("skip_countries"), warnings, "Never search in"),
            "profile.quick_win_no_site": f.get("quick_win_no_site", "").strip(),
            "engine.timezone": f.get("timezone", "").strip() or "UTC",
            "channels.enabled": f.getlist("channels") or ["email"],
            "channels.tasks_per_day": int(f.get("tasks_per_day") or 25),
            "channels.min_score_for_tasks": int(f.get("min_score_for_tasks") or 40),
            "sending.mode": "smtp" if f.get("live") == "1" else "dry_run",
            "sending.daily_cap": int(f.get("daily_cap") or 40),
            "sending.new_leads_per_day": int(f.get("new_leads_per_day") or 15),
            "notifications.owner_email": f.get("owner_email", "").strip(),
        }
        if f.get("llm_provider"):
            changes["llm.provider"] = f["llm_provider"]
        changes = {k: v for k, v in changes.items() if v != "" or k.startswith("notifications.")}
        # Service type → problem weights. Picking a new type loads its weights; otherwise your edited numbers are kept.
        new_type = f.get("service_type") or old_type
        changes["profile.service_type"] = new_type
        if new_type != old_type and new_type in PRESETS:
            changes["signals"] = weights_for(new_type)
            pq = PRESETS[new_type]["quick_win_no_site"]
            if f.get("quick_win_no_site", "").strip() in ("", PRESETS.get(old_type, {}).get("quick_win_no_site", "")):
                changes["profile.quick_win_no_site"] = pq
        else:
            weights = {}
            for k in SIGNALS:
                v = f.get(f"w_{k}")
                if v not in (None, ""):
                    try:
                        weights[k] = int(float(v))
                    except ValueError:
                        pass
            if weights:
                changes["signals"] = {**old_signals, **weights}
        start_cc = country_code_for(changes.get("reach.start_area", "").split(",")[-1]) if "," in changes.get("reach.start_area", "") else None
        if start_cc and start_cc in changes.get("reach.skip_countries", []):
            changes["reach.skip_countries"] = [c for c in changes["reach.skip_countries"] if c != start_cc]
            warnings.append(f"'Never search in' contained your own start country ({start_cc}); I removed it so your start area still works.")
        if "@" in f.get("FROM_EMAIL", "") and f.get("FROM_EMAIL", "").lower().endswith(("@gmail.com", "@yahoo.com", "@outlook.com", "@hotmail.com")):
            warnings.append("You're sending from a personal address. Fine for testing; for real cold email use a mailbox on your own "
                            "separate domain (Google Workspace), or the account may be limited or suspended.")
        try:
            save_settings(cfg, changes)
            env_changes = {k: f.get(k, "").strip() for k in
                           ("FROM_EMAIL", "FROM_NAME", "SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USER", "IMAP_HOST",
                            "IMAP_PORT", "SERPER_API_KEY", "GOOGLE_PLACES_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
            for secret in ("SMTP_PASSWORD", "ANTHROPIC_API_KEY", "IMAP_PASSWORD"):
                if f.get(secret, "").strip():
                    env_changes[secret] = f.get(secret).strip()
            providers = ["osm"] + [p for p, k in (("serper", "SERPER_API_KEY"), ("google_places", "GOOGLE_PLACES_API_KEY"))
                                   if (f.get(k) or env_file_values(cfg).get(k))]
            save_settings(cfg, {"sources.providers": providers})
            save_env(cfg, env_changes)
            from ..discovery import seed_areas
            seed_areas(db, cfg)
            if start_cc or changes.get("reach.skip_countries") is not None:
                skip = set(cfg.get("reach.skip_countries") or [])
                for a in db.all("SELECT id, country, status FROM areas WHERE status IN ('pending','skipped')"):
                    if a["country"] in skip and a["status"] == "pending":
                        db.update("areas", a["id"], {"status": "skipped", "note": "country skipped in settings"})
                    elif a["country"] not in skip and a["status"] == "skipped":
                        db.update("areas", a["id"], {"status": "pending", "note": None})
            msg = "Saved. The engine uses the new settings within a minute."
            changed_targeting = (dict(cfg.section("signals")) != old_signals or new_type != old_type
                                 or list(cfg.get("reach.business_types") or []) != old_types)
            if f.get("recheck") == "1" or changed_targeting:
                from ..enrich import rescore_all
                from ..writer import rewrite_waiting
                r = rescore_all(db, cfg)
                n = rewrite_waiting(db, cfg, writer())
                msg += (f" Re-checked your leads: {r['qualified']} now qualify, {r['set_aside']} set aside; "
                        f"{n} waiting messages rewritten with the new wording.")
            flash(msg, "ok")
            for w in warnings:
                flash(w, "error")
        except Exception as exc:
            log.exception("settings save failed")
            flash(f"Could not save: {exc}", "error")
        return redirect(url_for("settings", tab=f.get("tab", "setup")))

    @app.post("/settings/toggle/<key>")
    def toggle(key: str):
        if key not in ("sending_paused", "autopilot_cold", "autopilot_replies"):
            abort(400)
        st = engine_status(db, cfg)
        cur = {"sending_paused": st["paused"], "autopilot_cold": st["autopilot_cold"],
               "autopilot_replies": st["autopilot_replies"]}[key]
        db.kv_set(key, not cur)
        db.log("settings", f"{key} -> {not cur} (dashboard)")
        flash(f"{key.replace('_', ' ').capitalize()}: {'ON' if not cur else 'OFF'}", "ok")
        return redirect(request.referrer or url_for("settings"))

    @app.post("/settings/area")
    def settings_area():
        from ..discovery import add_area
        name = request.form.get("name", "").strip()
        if "," not in name:
            flash("Write it as 'City, Country', e.g. 'Ahmedabad, India'.", "error")
        else:
            add_area(db, name, first=True)
            db.execute("UPDATE areas SET status='pending' WHERE status='active' AND lower(name) != lower(?)", (name,))
            flash(f"'{name}' will be searched next.", "ok")
        return redirect(url_for("settings", tab="reach"))

    @app.post("/settings/area/<int:aid>/<action>")
    def settings_area_action(aid: int, action: str):
        a = db.one("SELECT * FROM areas WHERE id=?", (aid,)) or abort(404)
        if action == "skip":
            db.update("areas", aid, {"status": "skipped", "note": "skipped by you"})
        elif action == "next":
            from ..discovery import add_area
            add_area(db, a["name"], first=True)
            db.execute("UPDATE areas SET status='pending' WHERE status='active' AND id != ?", (aid,))
        elif action == "redo":
            db.update("areas", aid, {"status": "pending", "tile": 0, "note": None})
        return redirect(url_for("settings", tab="reach"))

    @app.post("/settings/import")
    def settings_import():
        from ..discovery.csv_import import import_csv
        url = request.form.get("url", "").strip()
        upload = request.files.get("file")
        prio = request.form.get("priority") == "1"
        area = request.form.get("area", "").strip() or None
        try:
            if upload and upload.filename:
                text = upload.read().decode("utf-8-sig", errors="replace")
                st = import_csv(db, cfg, text=text, area=area, priority=prio, source="csv")
            elif url:
                st = import_csv(db, cfg, url, area=area, priority=prio, source="sheet")
                if request.form.get("keep_synced") == "1":
                    sheets = list(cfg.get("sources.google_sheets") or [])
                    if not any((s.get("url") if isinstance(s, dict) else s) == url for s in sheets):
                        sheets.append({"url": url, "priority": prio, **({"area": area} if area else {})})
                        save_settings(cfg, {"sources.google_sheets": sheets})
            else:
                raise ValueError("Choose a CSV file or paste a Google Sheets link.")
            flash(f"Imported: {st['companies']} new businesses, {st['contacts']} with email, {st['updated']} already known. "
                  "They're being researched now.", "ok")
        except Exception as exc:
            flash(f"Import failed: {exc}", "error")
        return redirect(url_for("settings", tab="import"))

    @app.post("/settings/suppress")
    def add_suppression():
        v = request.form.get("value", "").strip().lower()
        if v:
            suppress(db, v, "added by you")
            for c in db.all("SELECT id FROM contacts WHERE email = ? OR (? LIKE '@%' AND email LIKE '%' || ?)", (v, v, v)):
                set_stage(db, cfg, c["id"], "unsubscribed", "added to do-not-contact list")
            flash(f"{v} will never be contacted.", "ok")
        return redirect(url_for("settings", tab="dnc"))

    @app.post("/run/<job>")
    def run_job(job: str):
        from ..discovery import run_discovery
        from ..enrich import enrich_pending
        from ..handler import process_inbox
        from ..pipeline import enroll_new_contacts
        from ..writer import write_due
        jobs = {
            "discover": lambda: run_discovery(db, cfg, force=True),
            "enrich": lambda: enrich_pending(db, cfg, None, writer().llm),
            "write": lambda: (enroll_new_contacts(db, cfg), write_due(db, cfg, writer())),
            "inbox": lambda: process_inbox(db, cfg, writer(), writer().llm),
            "onboarding": lambda: onboarding.tick(db, cfg, writer()),
        }
        if job not in jobs:
            abort(404)

        def _bg():
            try:
                jobs[job]()
            except Exception as exc:
                db.log("dashboard", f"manual run '{job}' failed: {exc}", level="error")
        threading.Thread(target=_bg, daemon=True).start()
        flash("Started in the background. Refresh in a minute.", "ok")
        return redirect(request.referrer or url_for("home"))

    # =========================================================================
    # Public: unsubscribe & booking webhook
    # =========================================================================
    @app.route("/u/<token>", methods=["GET", "POST"], endpoint="unsubscribe")
    def unsubscribe(token: str):
        cid = verify_unsub_token(db, cfg, token)
        ct = db.contact(cid) if cid else None
        if not ct:
            return render_template("unsubscribed.html", ok=False, profile=cfg.section("profile"))
        if request.method == "POST":  # one-click (RFC 8058) or the confirm button
            if ct.get("email"):
                suppress(db, ct["email"], "unsubscribe link")
            if ct["stage"] not in ("won", "client"):
                set_stage(db, cfg, cid, "unsubscribed", "unsubscribe link")
            else:
                db.update("contacts", cid, {"sequence": None, "next_action_at": None})
            return render_template("unsubscribed.html", ok=True, done=True, profile=cfg.section("profile"))
        return render_template("unsubscribed.html", ok=True, done=False, token=token, profile=cfg.section("profile"))

    @app.post("/webhooks/booking", endpoint="booking_webhook")
    def booking_webhook():
        expected = cfg.env("BOOKING_WEBHOOK_TOKEN")
        if not expected or not hmac.compare_digest(request.args.get("token", ""), expected):
            abort(403)
        payload = request.get_json(silent=True) or {}
        emails, start_time = set(), None

        def walk(node):
            nonlocal start_time
            if isinstance(node, dict):
                for k, v in node.items():
                    if isinstance(v, str) and "@" in v and "." in v.split("@")[-1] and len(v) < 120:
                        emails.add(v.strip().lower())
                    if k in ("startTime", "start_time", "start") and isinstance(v, str) and not start_time:
                        start_time = v
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(payload)
        matched = []
        for e in emails:
            ct = db.one("SELECT * FROM contacts WHERE email = ?", (e,))
            if ct and ct["stage"] not in ("won", "client", "unsubscribed"):
                start = parse_iso(start_time.replace("Z", "+00:00")) if start_time else None
                set_stage(db, cfg, ct["id"], "meeting", "booked via calendar", start_at=start)
                matched.append(ct["id"])
                from ..notify import notify
                notify(db, cfg, "📅 Call booked", f"{e} booked a call{(' for ' + start_time) if start_time else ''}.")
        return {"ok": True, "matched_contacts": matched}

    return app


def _codes(values: list[str], warnings: list[str], field: str) -> list[str]:
    """'India', 'uae', 'IN' -> ['IN', 'AE']. Unknown names are reported, not saved."""
    from ..geo import country_code_for
    out = []
    for v in values:
        code = country_code_for(v)
        if code and len(code) == 2 and code.isalpha():
            if code not in out:
                out.append(code)
        else:
            warnings.append(f"'{v}' in '{field}' isn't a country I know. Use 2-letter codes like IN, AE, GB, US.")
    return out


PLACEHOLDERS = {"Your Name", "Your Studio", "https://cal.com/your-name/15min",
                "Your Studio, Your Street, Your City, PIN, Country"}


def setup_needed(cfg: Config) -> bool:
    p = cfg.section("profile")
    return any(str(p.get(k, "")).strip() in PLACEHOLDERS for k in ("your_name", "business_name", "booking_link",
                                                                  "physical_address"))


def _float(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def serve(app: Flask, cfg: Config, host: str | None = None, port: int | None = None, quiet: bool = False) -> None:
    host = host or cfg.get("dashboard.host", "127.0.0.1")
    port = int(port or cfg.get("dashboard.port", 8787))
    if host not in ("127.0.0.1", "localhost") and not cfg.env("DASHBOARD_PASSWORD"):
        raise SystemExit("Refusing to open the dashboard to the network without DASHBOARD_PASSWORD in .env")
    if not quiet:
        print(f"Dashboard: http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}")
    log.info("dashboard on %s:%s", host, port)
    try:
        from waitress import serve as wserve  # production server
        wserve(app, host=host, port=port, threads=8, _quiet=quiet)
    except ImportError:
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


__all__ = ["create_app", "serve", "now_iso"]
