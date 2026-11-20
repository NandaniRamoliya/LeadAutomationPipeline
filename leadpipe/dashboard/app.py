"""Web dashboard: your daily 15-minute control room.

Private by default (127.0.0.1). If you expose it (0.0.0.0 / a domain), DASHBOARD_PASSWORD is required.
Two routes are public on purpose: /u/<token> (unsubscribe) and /webhooks/booking (calendar bookings).
"""
from __future__ import annotations

import hmac
import json
import logging
import secrets
import threading
from datetime import timedelta

from flask import Flask, Response, abort, flash, redirect, render_template, request, session, url_for

from .. import compliance, onboarding
from ..analytics import by_category, by_signal, by_step, companies_by_status, funnel, overview
from ..classifier import INTENTS, Classification
from ..config import Config
from ..db import DB, iso, jloads, parse_iso, utcnow
from ..engine import engine_status
from ..handler import apply_intent
from ..inbox import InboundMail
from ..pipeline import STAGES, advance_after_send, set_stage, suppress
from ..sender import mark_manual_done, verify_unsub_token
from ..templates import SIGNALS, Draft
from ..writer import Writer, queue_message, regenerate

log = logging.getLogger("leadpipe.dashboard")
PUBLIC_ENDPOINTS = {"unsubscribe", "booking_webhook", "static", "health"}


def create_app(cfg: Config, db: DB) -> Flask:
    app = Flask(__name__)
    app.secret_key = cfg.env("LEADPIPE_SECRET") or db.kv_get("flask_secret") or secrets.token_hex(32)
    db.kv_set("flask_secret", app.secret_key)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    password = cfg.env("DASHBOARD_PASSWORD")
    writer_holder: dict = {}

    def writer() -> Writer:
        if "w" not in writer_holder:
            writer_holder["w"] = Writer(db, cfg)
        return writer_holder["w"]

    # ---- security --------------------------------------------------------------
    @app.before_request
    def _auth():
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if password:
            a = request.authorization
            if not a or not hmac.compare_digest(a.password or "", password):
                return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Lead Pipeline"'})
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
                "jloads": jloads, "profile": cfg.section("profile"),
                "waiting": overview(db, cfg, 7)["waiting_on_you"]}

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

    @app.template_filter("evidence")
    def _evidence(value):
        if isinstance(value, dict):
            return ", ".join(str(v) for v in value.values())
        return value

    @app.template_filter("short")
    def _short(value, n=90):
        value = (value or "").replace("\n", " ")
        return value if len(value) <= n else value[: n - 1] + "…"

    # ---- overview -----------------------------------------------------------------
    @app.get("/")
    def home():
        events = db.all("SELECT * FROM events ORDER BY id DESC LIMIT 25")
        return render_template("home.html", o=overview(db, cfg, 30), funnel=funnel(db), events=events,
                               companies=companies_by_status(db))

    @app.get("/health")
    def health():
        st = engine_status(db, cfg)
        return {"ok": True, "engine_alive": st["alive"], "heartbeat": st["heartbeat"]}

    # ---- outbox (approve / edit / reject) ----------------------------------------
    @app.get("/outbox")
    def outbox():
        rows = db.all("""SELECT m.*, co.name company, co.website, ct.full_name, ct.email, ct.stage
                         FROM messages m LEFT JOIN companies co ON co.id = m.company_id LEFT JOIN contacts ct ON ct.id = m.contact_id
                         WHERE m.direction='out' AND m.status='pending' AND m.channel='email'
                         ORDER BY CASE m.kind WHEN 'reply' THEN 0 WHEN 'onboarding' THEN 1 ELSE 2 END, m.scheduled_for LIMIT 200""")
        approved = db.all("""SELECT m.id, m.subject, m.to_addr, m.scheduled_for, co.name company FROM messages m
                             LEFT JOIN companies co ON co.id = m.company_id WHERE m.direction='out' AND m.status='approved'
                             AND m.channel='email' ORDER BY m.scheduled_for LIMIT 100""")
        return render_template("outbox.html", rows=rows, approved=approved)

    def _save_edits(mid: int) -> dict | None:
        m = db.message(mid)
        if not m:
            return None
        subject = request.form.get("subject", m["subject"] or "")
        body = request.form.get("body", m["body"] or "")
        company = db.company(m["company_id"]) or {}
        issues = compliance.check_message(cfg, subject, body, m["purpose"], company.get("name", ""), kind=m["kind"])
        db.update("messages", mid, {"subject": subject, "body": body, "check_issues": json.dumps(issues)})
        return db.message(mid)

    @app.post("/outbox/<int:mid>/<action>")
    def outbox_action(mid: int, action: str):
        m = _save_edits(mid)
        if not m:
            abort(404)
        issues = jloads(m["check_issues"], [])
        if action == "approve":
            if compliance.blocking(issues):
                flash("Not approved: " + "; ".join(i["detail"] for i in issues if i["level"] == "block"), "error")
            else:
                db.update("messages", mid, {"status": "approved", "needs_human": 0})
                flash("Approved. It will go out at the next allowed time.", "ok")
        elif action == "save":
            flash("Saved.", "ok")
        elif action == "reject":
            new = regenerate(db, cfg, mid, writer())
            flash("Rejected." + (" A fresh draft was written." if new else ""), "ok")
        elif action == "skip":
            db.update("messages", mid, {"status": "cancelled", "error": "skipped by you"})
            if m["kind"] == "sequence":
                advance_after_send(db, cfg, m)
            flash("Skipped. The next follow-up is scheduled.", "ok")
        elif action == "discard":
            db.update("messages", mid, {"status": "cancelled", "error": "discarded by you"})
            flash("Discarded.", "ok")
        elif action == "stop":
            set_stage(db, cfg, m["contact_id"], "paused", "stopped by you")
            flash("Contact paused: no more emails to them.", "ok")
        return redirect(request.referrer or url_for("outbox"))

    @app.post("/outbox/approve-all")
    def approve_all():
        n = 0
        for r in db.all("SELECT id, check_issues FROM messages WHERE direction='out' AND status='pending' AND channel='email' AND kind='sequence'"):
            if not compliance.blocking(jloads(r["check_issues"], [])):
                db.update("messages", r["id"], {"status": "approved"})
                n += 1
        flash(f"Approved {n} outreach emails that passed every check (replies still need you one by one).", "ok")
        return redirect(url_for("outbox"))

    @app.post("/approved/<int:mid>/unapprove")
    def unapprove(mid: int):
        db.update("messages", mid, {"status": "pending"})
        flash("Moved back to the Outbox.", "ok")
        return redirect(url_for("outbox"))

    # ---- replies -------------------------------------------------------------------
    @app.get("/replies")
    def replies():
        rows = db.all("""SELECT m.*, co.name company, ct.full_name, ct.email, ct.stage, ct.id cid FROM messages m
                         LEFT JOIN companies co ON co.id = m.company_id LEFT JOIN contacts ct ON ct.id = m.contact_id
                         WHERE m.direction='in' AND m.received_at >= ? ORDER BY m.id DESC LIMIT 200""",
                      (iso(utcnow() - timedelta(days=45)),))
        return render_template("replies.html", rows=rows)

    # ---- manual DMs / calls --------------------------------------------------------
    @app.get("/manual")
    def manual():
        rows = db.all("""SELECT m.*, co.name company, co.phone, co.instagram, co.website, co.facebook, ct.id cid
                         FROM messages m JOIN contacts ct ON ct.id = m.contact_id LEFT JOIN companies co ON co.id = m.company_id
                         WHERE m.direction='out' AND m.channel='manual' AND m.status IN ('pending','approved')
                         ORDER BY m.scheduled_for LIMIT 200""")
        return render_template("manual.html", rows=rows)

    @app.post("/manual/<int:mid>/done")
    def manual_done(mid: int):
        m = _save_edits(mid)
        if m:
            mark_manual_done(db, cfg, mid)
            flash("Logged as sent. The next follow-up is scheduled.", "ok")
        return redirect(url_for("manual"))

    # ---- leads & contacts ---------------------------------------------------------
    @app.get("/leads")
    def leads():
        status = request.args.get("status")
        q = request.args.get("q", "").strip()
        sql = """SELECT co.*, (SELECT COUNT(*) FROM contacts WHERE company_id = co.id) n_contacts,
                 (SELECT GROUP_CONCAT(stage, ',') FROM contacts WHERE company_id = co.id) stages FROM companies co WHERE 1=1"""
        params: list = []
        if status:
            sql += " AND co.status = ?"
            params.append(status)
        if q:
            sql += " AND (co.name LIKE ? OR co.website LIKE ? OR co.category LIKE ?)"
            params += [f"%{q}%"] * 3
        rows = db.all(sql + " ORDER BY co.score DESC, co.id DESC LIMIT 300", params)
        return render_template("leads.html", rows=rows, status=status, q=q, counts=companies_by_status(db))

    @app.get("/contacts")
    def contacts():
        stage = request.args.get("stage")
        sql = """SELECT ct.*, co.name company FROM contacts ct LEFT JOIN companies co ON co.id = ct.company_id"""
        params: list = []
        if stage:
            sql += " WHERE ct.stage = ?"
            params.append(stage)
        rows = db.all(sql + " ORDER BY ct.updated_at DESC LIMIT 300", params)
        return render_template("contacts.html", rows=rows, stage=stage, funnel=funnel(db))

    @app.get("/company/<int:cid>")
    def company(cid: int):
        co = db.company(cid) or abort(404)
        cts = db.all("SELECT * FROM contacts WHERE company_id = ?", (cid,))
        if len(cts) == 1:
            return redirect(url_for("contact", cid=cts[0]["id"]))
        return render_template("company.html", co=co, contacts=cts, SIGNALS=SIGNALS)

    @app.get("/contact/<int:cid>")
    def contact(cid: int):
        ct = db.contact(cid) or abort(404)
        co = db.company(ct["company_id"]) or {}
        msgs = db.all("SELECT * FROM messages WHERE contact_id = ? ORDER BY id", (cid,))
        deal = db.one("SELECT * FROM deals WHERE contact_id = ?", (cid,))
        events = db.all("SELECT * FROM events WHERE contact_id = ? OR company_id = ? ORDER BY id DESC LIMIT 30", (cid, ct["company_id"]))
        colleagues = db.all("SELECT id, full_name, email, stage FROM contacts WHERE company_id = ? AND id != ?", (ct["company_id"], cid))
        return render_template("contact.html", ct=ct, co=co, msgs=msgs, deal=deal, events=events, colleagues=colleagues,
                               SIGNALS=SIGNALS, CHECKLIST=onboarding.CHECKLIST, MANUAL_KEYS=onboarding.MANUAL_KEYS)

    @app.post("/contact/<int:cid>/<action>")
    def contact_action(cid: int, action: str):
        ct = db.contact(cid) or abort(404)
        co = db.company(ct["company_id"]) or {}
        try:
            if action == "stage":
                st = request.form["stage"]
                if st == "won":
                    onboarding.start(db, cfg, cid, _float(request.form.get("value")))
                    flash("Deal WON! Onboarding started: the contract email is in your Outbox.", "ok")
                else:
                    set_stage(db, cfg, cid, st, request.form.get("reason") or "set by you")
                    flash(f"Moved to '{STAGES[st]}'. Its follow-ups are scheduled.", "ok")
            elif action == "won":
                onboarding.start(db, cfg, cid, _float(request.form.get("value")), request.form.get("currency") or None)
                flash("Deal WON! Onboarding started: the contract email is in your Outbox.", "ok")
            elif action == "note":
                note = request.form.get("note", "").strip()
                db.update("contacts", cid, {"notes": ((ct.get("notes") or "") + "\n" + note).strip()})
                db.log("note", note, contact_id=cid)
            elif action == "log-reply":
                text = request.form.get("text", "").strip()
                intent = request.form.get("intent") or ""
                from ..classifier import classify
                result = Classification(intent, 1.0, "you") if intent else classify(cfg, text)
                mail = InboundMail(message_id=f"<manual-{secrets.token_hex(6)}@local>", from_addr=ct.get("email") or "",
                                   from_name=ct.get("full_name") or "", subject="Reply logged by you", body=text)
                r = apply_intent(db, cfg, ct, mail, result, writer())
                flash(f"Logged as '{INTENTS.get(r['intent'], r['intent'])}'. A reply draft is ready if one is needed.", "ok")
            elif action == "compose":
                draft = Draft(subject=request.form.get("subject", ""), body=request.form.get("body", ""), generator="you")
                queue_message(db, cfg, ct, co, draft, "reply")
                flash("Added to the Outbox for approval.", "ok")
            elif action == "suppress":
                if ct.get("email"):
                    suppress(db, ct["email"], "marked do-not-contact by you")
                set_stage(db, cfg, cid, "unsubscribed", "do-not-contact (you)")
                flash("Added to the do-not-contact list.", "ok")
            elif action == "onboard":
                onboarding.set_step(db, cfg, cid, request.form["key"], request.form.get("done") == "1")
                flash("Onboarding updated.", "ok")
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("contact", cid=cid))

    # ---- clients (onboarding) -------------------------------------------------------
    @app.get("/clients")
    def clients():
        deals = db.all("""SELECT d.*, co.name company, ct.full_name, ct.email, ct.stage FROM deals d
                          JOIN contacts ct ON ct.id = d.contact_id LEFT JOIN companies co ON co.id = d.company_id
                          ORDER BY d.won_at DESC""")
        for d in deals:
            d["done"], d["total"] = onboarding.progress(d)
        return render_template("clients.html", deals=deals, CHECKLIST=onboarding.CHECKLIST, MANUAL_KEYS=onboarding.MANUAL_KEYS)

    # ---- analytics ----------------------------------------------------------------------
    @app.get("/analytics")
    def analytics():
        days = int(request.args.get("days", 30))
        return render_template("analytics.html", o=overview(db, cfg, days), steps=by_step(db), signals=by_signal(db),
                               categories=by_category(db), days=days, SIGNALS=SIGNALS)

    # ---- settings & manual runs ---------------------------------------------------------
    @app.get("/settings")
    def settings():
        return render_template("settings.html", cfg=cfg, suppression=db.all("SELECT * FROM suppression ORDER BY created_at DESC LIMIT 100"),
                               targets=cfg.get("targets") or [])

    @app.post("/settings/toggle/<key>")
    def toggle(key: str):
        if key not in ("sending_paused", "autopilot_cold", "autopilot_replies"):
            abort(400)
        current = engine_status(db, cfg)
        cur = {"sending_paused": current["paused"], "autopilot_cold": current["autopilot_cold"],
               "autopilot_replies": current["autopilot_replies"]}[key]
        db.kv_set(key, not cur)
        db.log("settings", f"{key} -> {not cur} (dashboard)")
        flash(f"{key.replace('_', ' ')}: {'ON' if not cur else 'OFF'}", "ok")
        return redirect(request.referrer or url_for("settings"))

    @app.post("/settings/suppress")
    def add_suppression():
        v = request.form.get("value", "").strip().lower()
        if v:
            suppress(db, v, "added by you")
            for c in db.all("SELECT id FROM contacts WHERE email = ? OR (? LIKE '@%' AND email LIKE '%' || ?)", (v, v, v)):
                set_stage(db, cfg, c["id"], "unsubscribed", "added to do-not-contact list")
            flash(f"{v} will never be emailed.", "ok")
        return redirect(url_for("settings"))

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
        flash(f"Started '{job}' in the background. Refresh in a minute.", "ok")
        return redirect(request.referrer or url_for("home"))

    # ---- public: unsubscribe & booking webhook ------------------------------------------
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


def _float(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def serve(app: Flask, cfg: Config, host: str | None = None, port: int | None = None) -> None:
    host = host or cfg.get("dashboard.host", "127.0.0.1")
    port = int(port or cfg.get("dashboard.port", 8787))
    if host not in ("127.0.0.1", "localhost") and not cfg.env("DASHBOARD_PASSWORD"):
        raise SystemExit("Refusing to open the dashboard to the network without DASHBOARD_PASSWORD in .env")
    print(f"Dashboard: http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}")
    try:
        from waitress import serve as wserve  # optional production server
        wserve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
