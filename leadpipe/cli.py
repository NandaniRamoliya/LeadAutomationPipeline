"""Command line: `leadpipe <command>`. Run `leadpipe --help` for the list."""
from __future__ import annotations

import argparse
import csv
import json
import logging
import shutil
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, load_config
from .db import DB, jloads

ENV_TEMPLATE = Path(__file__).resolve().parent.parent / ".env.example"


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("urllib3", "httpx", "httpx2", "anthropic", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _ctx(args):
    cfg = load_config(args.config)
    db = DB(cfg.db_path)
    return cfg, db


def _print_table(rows: list[dict], cols: list[str], widths: dict[str, int] | None = None) -> None:
    widths = widths or {}
    if not rows:
        print("(nothing)")
        return
    w = {c: min(max(len(c), *(len(str(r.get(c) or "")) for r in rows)), widths.get(c, 40)) for c in cols}
    print("  ".join(c.upper().ljust(w[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c) if r.get(c) is not None else "").replace("\n", " ")[: w[c]].ljust(w[c]) for c in cols))


# ---------------------------------------------------------------------------
def _set_line(text: str, key: str, value: str, count: int = 1) -> str:
    """Replace the value of the first `key:` line(s) in the YAML text, keeping indentation and comments."""
    import re
    rx = re.compile(rf"^(\s*{re.escape(key)}:\s*)(\"[^\"]*\"|\[[^\]]*\]|[^#\n]*?)(\s+#.*)?$", re.M)
    return rx.sub(lambda m: m.group(1) + value + (m.group(3) or ""), text, count=count)


def cmd_init(args) -> None:
    target = Path(args.config or "config.yaml")
    env_path = target.parent / ".env"
    if target.exists() and not args.force:
        print(f"{target} already exists (use --force to overwrite).")
    else:
        text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
        if not args.yes and sys.stdin.isatty():
            print("Answer a few questions (Enter keeps the example value). You can edit config.yaml later.\n")
            for key, q in [("your_name", "Your name"), ("business_name", "Business name"),
                           ("website", "Your website"), ("service", "What you sell, in plain words"),
                           ("starter_offer", "Your starter offer"), ("price_range", "Typical price range"),
                           ("booking_link", "Calendar booking link"), ("physical_address", "Postal address (legal footer)")]:
                ans = input(f"{q}: ").strip()
                if ans:
                    text = _set_line(text, key, json.dumps(ans, ensure_ascii=False))
            proof = input("One real result you can show (e.g. 'built sites for 5 cafes'), or blank: ").strip()
            if proof:
                text = text.replace('    - "built booking websites for 12 local clinics"', "    - " + json.dumps(proof, ensure_ascii=False), 1)
            area = input("Where to start, 'City, Country' (e.g. 'Surat, India'): ").strip()
            cats = input("Business types, comma separated (e.g. dentist, cafe, gym): ").strip()
            tzname = input("Your timezone (e.g. Asia/Kolkata, Europe/London, America/New_York): ").strip()
            if area:
                text = _set_line(text, "start_area", json.dumps(area, ensure_ascii=False))
            if cats:
                text = _set_line(text, "business_types", json.dumps([c.strip() for c in cats.split(",") if c.strip()]))
            if tzname:
                text = _set_line(text, "timezone", json.dumps(tzname))
        target.write_text(text, encoding="utf-8")
        print(f"Created {target}")
    if not env_path.exists():
        if ENV_TEMPLATE.exists():
            shutil.copy(ENV_TEMPLATE, env_path)
            print(f"Created {env_path}. Put your mailbox password / API keys there.")
    print("\nNext: edit config.yaml and .env, then run `leadpipe doctor`.")


def cmd_doctor(args) -> None:
    from .doctor import run_doctor
    cfg, db = _ctx(args)
    ok = run_doctor(cfg, db, check_network=not args.offline)
    sys.exit(0 if ok else 1)


def cmd_signals(args) -> None:
    from .templates import SIGNALS
    cfg = load_config(args.config)
    weights = cfg.section("signals")
    rows = [{"signal": k, "weight": weights.get(k, 0), "label": v["label"], "meaning": v["explain"]} for k, v in SIGNALS.items()]
    _print_table(sorted(rows, key=lambda r: -float(r["weight"] or 0)), ["signal", "weight", "label", "meaning"],
                 {"meaning": 70})
    print("\nChange weights under `signals:` in config.yaml. 0 = ignore. Higher = more important for YOUR service.")


def cmd_categories(args) -> None:
    from .discovery.osm import CATEGORY_TAGS
    for k, v in CATEGORY_TAGS.items():
        print(f"{k:20} {', '.join(v)}")
    print('\nAnything else: use a raw OpenStreetMap tag, e.g. "shop=bicycle".')


def cmd_discover(args) -> None:
    from .discovery import run_discovery
    cfg, db = _ctx(args)
    n = run_discovery(db, cfg, area_name=args.area, force=True, steps=args.steps)
    print(f"{n} new businesses added. Next: `leadpipe enrich` (the engine does this by itself).")


def cmd_areas(args) -> None:
    from .discovery import add_area, current_area, seed_areas
    cfg, db = _ctx(args)
    seed_areas(db, cfg)
    if args.action == "add":
        add_area(db, args.name, first=True)
        db.execute("UPDATE areas SET status='pending' WHERE status='active' AND lower(name) != lower(?)", (args.name,))
        print(f"'{args.name}' will be searched next.")
        return
    if args.action == "skip":
        row = db.one("SELECT id FROM areas WHERE lower(name) = lower(?)", (args.name,))
        if row:
            db.update("areas", row["id"], {"status": "skipped", "note": "skipped by you"})
            print("Skipped.")
        return
    current_area(db)
    rows = db.all("SELECT name, status, tile, tiles, found FROM areas ORDER BY CASE status WHEN 'active' THEN 0 "
                  "WHEN 'pending' THEN 1 ELSE 2 END, position LIMIT ?", (args.limit,))
    for r in rows:
        r["progress"] = f"{r['tile']}/{r['tiles'] or '?'}"
    _print_table(rows, ["name", "status", "progress", "found"])


def cmd_import(args) -> None:
    from .discovery.csv_import import import_csv
    cfg, db = _ctx(args)
    st = import_csv(db, cfg, args.file, area=args.area, priority=args.priority,
                    source="sheet" if "docs.google.com" in args.file else "csv")
    print(f"{st['rows']} rows: {st['companies']} new businesses, {st['contacts']} contacts with email, "
          f"{st['updated']} already known, {st['skipped']} skipped.")
    print("Columns used: " + ", ".join(f"{k}←'{v}'" for k, v in st["columns"].items()))


def cmd_enrich(args) -> None:
    from .enrich import enrich_pending
    from .llm import LLM
    cfg, db = _ctx(args)
    n = enrich_pending(db, cfg, args.limit, llm=LLM(cfg))
    rows = db.all("SELECT name, status, score, status_reason FROM companies ORDER BY enriched_at DESC LIMIT ?", (n,))
    _print_table(rows, ["name", "status", "score", "status_reason"])


def cmd_leads(args) -> None:
    cfg, db = _ctx(args)
    where, params = "", []
    if args.status:
        where, params = "WHERE co.status = ?", [args.status]
    rows = db.all(f"""SELECT co.id, co.name, co.category, co.score, co.status, co.website,
                      (SELECT GROUP_CONCAT(COALESCE(email, 'manual'), ' ') FROM contacts WHERE company_id = co.id) contacts
                      FROM companies co {where} ORDER BY co.score DESC LIMIT ?""", (*params, args.limit))
    _print_table(rows, ["id", "name", "category", "score", "status", "website", "contacts"], {"name": 30, "website": 30})


def cmd_contacts(args) -> None:
    cfg, db = _ctx(args)
    where, params = "", []
    if args.stage:
        where, params = "WHERE ct.stage = ?", [args.stage]
    rows = db.all(f"""SELECT ct.id, co.name company, ct.full_name, ct.email, ct.email_status, ct.stage, ct.sequence,
                      ct.step, ct.next_action_at FROM contacts ct JOIN companies co ON co.id = ct.company_id {where}
                      ORDER BY ct.updated_at DESC LIMIT ?""", (*params, args.limit))
    _print_table(rows, ["id", "company", "full_name", "email", "email_status", "stage", "sequence", "step", "next_action_at"],
                 {"company": 25})


def cmd_write(args) -> None:
    from .pipeline import enroll_new_contacts
    from .writer import write_due
    cfg, db = _ctx(args)
    e = enroll_new_contacts(db, cfg)
    n = write_due(db, cfg, limit=args.limit)
    print(f"Enrolled {e} new leads, wrote {n} drafts. Review: `leadpipe outbox` or the dashboard.")


def cmd_outbox(args) -> None:
    from .pipeline import set_stage
    from .writer import regenerate
    cfg, db = _ctx(args)
    action = args.action or "list"
    if action == "list":
        rows = db.all("""SELECT m.id, co.name company, m.to_addr, m.kind, m.purpose, m.subject, m.check_issues, m.scheduled_for
                         FROM messages m LEFT JOIN companies co ON co.id = m.company_id
                         WHERE m.direction='out' AND m.status='pending' AND m.channel='email'
                         ORDER BY m.scheduled_for LIMIT 100""")
        for r in rows:
            issues = jloads(r["check_issues"], [])
            blocks = [i["rule"] for i in issues if i["level"] == "block"]
            r["checks"] = "BLOCKED: " + ",".join(blocks) if blocks else ("ok" if not issues else "warn")
        _print_table(rows, ["id", "company", "to_addr", "kind", "purpose", "subject", "checks"], {"subject": 40, "company": 22})
        return
    if action == "show":
        m = db.message(args.id)
        if not m:
            sys.exit("not found")
        print(f"To: {m['to_addr']}\nSubject: {m['subject']}\n\n{m['body']}\n")
        for i in jloads(m["check_issues"], []):
            print(f"[{i['level']}] {i['detail']}")
        return
    if args.id == 0 and action != "approve":
        sys.exit(f"Give a message id: leadpipe outbox {action} ID")
    ids = [r["id"] for r in db.all("SELECT id, check_issues FROM messages WHERE direction='out' AND status='pending' AND channel='email'")
           if not any(i["level"] == "block" for i in jloads(r["check_issues"], []))] if args.id == 0 else [args.id]
    for mid in ids:
        if action == "approve":
            m = db.message(mid)
            if m and any(i["level"] == "block" for i in jloads(m["check_issues"], [])):
                print(f"{mid}: blocked by checks. Edit it in the dashboard first.")
                continue
            db.update("messages", mid, {"status": "approved", "needs_human": 0})
            print(f"{mid}: approved")
        elif action == "reject":
            new = regenerate(db, cfg, mid)
            print(f"{mid}: rejected" + (f", new draft {new}" if new else ""))
        elif action == "skip":
            m = db.message(mid)
            if m:
                db.update("messages", mid, {"status": "cancelled", "error": "skipped by you"})
                from .pipeline import advance_after_send
                advance_after_send(db, cfg, m)
                print(f"{mid}: skipped, next step scheduled")
        elif action == "stop":
            m = db.message(mid)
            if m:
                set_stage(db, cfg, m["contact_id"], "paused", "stopped by you")
                print(f"{mid}: contact paused")


def cmd_send(args) -> None:
    from .sender import Mailer, send_due
    cfg, db = _ctx(args)
    n = send_due(db, cfg, Mailer(cfg), respect_timing=not args.now, max_send=args.max)
    print(f"Sent {n} email(s) ({cfg.get('sending.mode')} mode).")


def cmd_inbox(args) -> None:
    from .handler import process_inbox
    cfg, db = _ctx(args)
    print(f"Handled {process_inbox(db, cfg)} new replies.")


def cmd_reply(args) -> None:
    from .handler import simulate_reply
    cfg, db = _ctx(args)
    cid = args.contact
    if not cid.isdigit():
        row = db.one("SELECT id FROM contacts WHERE email = ?", (cid.lower(),))
        if not row:
            sys.exit("contact not found")
        cid = row["id"]
    print(simulate_reply(db, cfg, int(cid), args.text))


def cmd_run(args) -> None:
    from .engine import Engine
    cfg, db = _ctx(args)
    eng = Engine(db, cfg)
    eng.install_signal_handlers()
    if args.once:
        print(eng.cycle())
        return
    eng.run_forever()


def cmd_dashboard(args) -> None:
    from .dashboard.app import create_app, serve
    cfg, db = _ctx(args)
    serve(create_app(cfg, db), cfg, args.host, args.port)


def cmd_start(args) -> None:
    """Engine (background thread) + dashboard in one process. What the background service, Docker and systemd run."""
    import threading
    from .dashboard.app import create_app, serve
    from .engine import Engine, _acquire_lock, engine_running_elsewhere
    from .service import setup_file_logging
    if not Path(args.config or "config.yaml").exists() and not args.config:
        cmd_init(argparse.Namespace(config=None, force=False, yes=True))
    cfg, db = _ctx(args)
    logfile = setup_file_logging(cfg, args.verbose)
    lock_path = cfg.data_dir / "engine.lock"
    other = engine_running_elsewhere(lock_path)
    if other:
        msg = f"Already running in the background (pid {other}). Dashboard: http://localhost:{args.port or cfg.get('dashboard.port', 8787)}"
        if args.background:
            logging.getLogger("leadpipe").info(msg)
        else:
            print(msg)
        return
    lock = _acquire_lock(lock_path)
    eng = Engine(db, cfg)
    t = threading.Thread(target=eng.run_forever, kwargs={"lock": lock}, name="engine", daemon=True)
    t.start()
    if not args.background:
        print(f"Running 24/7. Logs: {logfile}. Press Ctrl+C to stop.")
        print("To keep it running in the background after you close this window: leadpipe service install")
    try:
        serve(create_app(cfg, db, engine=eng), cfg, args.host, args.port, quiet=args.background)
    except (OSError, SystemExit) as exc:
        logging.getLogger("leadpipe").warning("dashboard not started (%s); engine keeps running", exc)
        while t.is_alive():
            t.join(timeout=60)
    finally:
        eng.stop_event.set()
        t.join(timeout=10)


def cmd_service(args) -> None:
    from . import service
    cfg, db = _ctx(args)
    if not cfg.path:
        sys.exit("Create your settings first: leadpipe init (or open the dashboard → Settings).")
    act = args.action
    if act == "install":
        print(service.install(cfg))
    elif act == "uninstall":
        print(service.uninstall(cfg))
    elif act == "stop":
        print(service.stop(cfg))
    elif act == "start":
        print(service.start(cfg))
    elif act == "logs":
        print(service.tail_log(cfg, args.lines))
    else:
        st = service.status(cfg)
        print(f"Installed as background service: {'yes' if st['installed'] else 'no'}")
        print(f"Engine running: {'yes (pid ' + str(st['pid']) + ')' if st['running'] else 'no'}")
        print(f"Log file: {st['log']}")


def cmd_open(args) -> None:
    import webbrowser
    cfg = load_config(args.config)
    url = f"http://localhost:{cfg.get('dashboard.port', 8787)}"
    webbrowser.open(url)
    print(url)


def cmd_sending(args) -> None:
    cfg, db = _ctx(args)
    db.kv_set("sending_paused", args.state == "pause")
    db.log("settings", f"sending {args.state}d from CLI")
    print(f"Sending {'PAUSED. Nothing will be sent.' if args.state == 'pause' else 'resumed.'}")


def cmd_autopilot(args) -> None:
    cfg, db = _ctx(args)
    key = "autopilot_replies" if args.replies else "autopilot_cold"
    db.kv_set(key, args.state == "on")
    db.log("settings", f"{key} {args.state}")
    print(f"{key} is now {args.state.upper()}.")


def cmd_stage(args) -> None:
    from .pipeline import set_stage
    cfg, db = _ctx(args)
    c = set_stage(db, cfg, args.contact, args.stage, args.reason or "set by you")
    print(f"{c['email'] or c['id']}: now {c['stage']}")


def cmd_won(args) -> None:
    from . import onboarding
    cfg, db = _ctx(args)
    d = onboarding.start(db, cfg, args.contact, args.value, args.currency)
    print(f"Deal won. Onboarding started (deal {d['id']}). The contract email is in your Outbox.")


def cmd_onboard(args) -> None:
    from . import onboarding
    cfg, db = _ctx(args)
    if not args.step:
        d = db.one("SELECT * FROM deals WHERE contact_id = ?", (args.contact,))
        if not d:
            sys.exit("No deal for this contact.")
        cl = jloads(d["checklist"], {})
        for key, label, who in onboarding.CHECKLIST:
            print(f"[{'x' if key in cl else ' '}] {label:35} {key:18} ({who})")
        return
    onboarding.set_step(db, cfg, args.contact, args.step, not args.undo)
    print(f"{args.step} {'undone' if args.undo else 'done'}.")


def cmd_stats(args) -> None:
    from .analytics import by_step, digest_text, funnel
    cfg, db = _ctx(args)
    print(digest_text(db, cfg))
    print()
    _print_table([f for f in funnel(db) if f["count"]], ["stage", "count", "label"])
    print()
    _print_table(by_step(db), ["sequence", "step", "purpose", "sent", "replies", "reply_rate"])


def cmd_export(args) -> None:
    cfg, db = _ctx(args)
    if args.calls:
        from .tasks import export_call_list
        rows = export_call_list(db, cfg)
        out = open(args.file, "w", newline="", encoding="utf-8") if args.file else sys.stdout
        w = csv.DictWriter(out, fieldnames=["company", "phone", "city", "score", "script", "task_id"])
        w.writeheader()
        w.writerows(rows)
        if args.file:
            out.close()
            print(f"Wrote {len(rows)} calls to {args.file}")
        return
    rows = db.all("""SELECT co.name company, co.category, co.website, co.phone, co.city, co.score, co.status company_status,
                     ct.full_name, ct.role, ct.email, ct.email_status, ct.stage, co.problems
                     FROM contacts ct JOIN companies co ON co.id = ct.company_id
                     WHERE (? IS NULL OR ct.stage = ?) ORDER BY co.score DESC""", (args.stage, args.stage))
    out = open(args.file, "w", newline="", encoding="utf-8") if args.file else sys.stdout
    w = csv.DictWriter(out, fieldnames=list(rows[0].keys()) if rows else ["company"])
    w.writeheader()
    w.writerows(rows)
    if args.file:
        out.close()
        print(f"Wrote {len(rows)} rows to {args.file}")


def cmd_suppress(args) -> None:
    from .pipeline import set_stage, suppress
    cfg, db = _ctx(args)
    value = args.value.lower()
    suppress(db, value, "added by you")
    for c in db.all("SELECT id FROM contacts WHERE email = ? OR (? LIKE '@%' AND email LIKE '%' || ?)", (value, value, value)):
        set_stage(db, cfg, c["id"], "unsubscribed", "added to do-not-contact list")
    print(f"{value} will never be emailed.")


def cmd_forget(args) -> None:
    """GDPR/DPDP 'right to erasure': delete personal data, keep only a hash so they're never re-added."""
    import hashlib
    from .pipeline import suppress
    cfg, db = _ctx(args)
    email = args.email.strip().lower()
    suppress(db, "sha256:" + hashlib.sha256(email.encode()).hexdigest(), "erasure request")
    db.execute("DELETE FROM suppression WHERE value = ?", (email,))
    for c in db.all("SELECT id FROM contacts WHERE email = ?", (email,)):
        db.execute("DELETE FROM messages WHERE contact_id = ?", (c["id"],))
        db.execute("DELETE FROM deals WHERE contact_id = ?", (c["id"],))
        db.execute("DELETE FROM events WHERE contact_id = ?", (c["id"],))
        db.execute("DELETE FROM contacts WHERE id = ?", (c["id"],))
    print(f"Deleted all data for {email}. Only a one-way hash remains so they are never contacted again.")


def cmd_demo(args) -> None:
    from .demo import make_demo
    root = make_demo(args.folder)
    print(f"Demo created in {root} (made-up businesses, nothing is ever sent).")
    print(f"Open it:  leadpipe -c {root / 'config.yaml'} dashboard")


def cmd_train(args) -> None:
    from .doctor import train_profile
    cfg, db = _ctx(args)
    print(train_profile(cfg, args.url))


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="leadpipe", description="24/7 lead generation & sales pipeline.")
    p.add_argument("-c", "--config", help="path to config.yaml (default ./config.yaml)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create config.yaml and .env (asks a few questions)")
    s.add_argument("--force", action="store_true")
    s.add_argument("-y", "--yes", action="store_true", help="no questions, copy the example")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("doctor", help="check settings, mailbox, DNS (SPF/DKIM/DMARC), AI")
    s.add_argument("--offline", action="store_true")
    s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("train", help="read your website and suggest your profile (needs AI)")
    s.add_argument("url")
    s.set_defaults(fn=cmd_train)
    s = sub.add_parser("demo", help="create a safe demo with made-up leads to explore the dashboard")
    s.add_argument("--folder", default="demo")
    s.set_defaults(fn=cmd_demo)
    sub.add_parser("signals", help="explain buying signals and your weights").set_defaults(fn=cmd_signals)
    sub.add_parser("categories", help="list business categories for targets").set_defaults(fn=cmd_categories)

    s = sub.add_parser("discover", help="find businesses in the current area now (or --area 'City, Country')")
    s.add_argument("--area")
    s.add_argument("--steps", type=int, default=1, help="how many pieces of the map to search")
    s.set_defaults(fn=cmd_discover)
    s = sub.add_parser("areas", help="the world rollout: list | add 'City, Country' | skip 'City, Country'")
    s.add_argument("action", nargs="?", choices=["list", "add", "skip"], default="list")
    s.add_argument("name", nargs="?")
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(fn=cmd_areas)
    s = sub.add_parser("import", help="import leads from a CSV file or a Google Sheet link")
    s.add_argument("file", help="path to .csv, or a Google Sheets link shared as 'Anyone with the link'")
    s.add_argument("--area", help="'City, Country' these leads are in (timezone/country)")
    s.add_argument("--priority", action="store_true", help="mark every row as high-value")
    s.set_defaults(fn=cmd_import)
    s = sub.add_parser("enrich", help="audit websites, find + verify emails, score leads")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn=cmd_enrich)
    s = sub.add_parser("leads", help="list companies")
    s.add_argument("--status", choices=["new", "qualified", "disqualified", "error"])
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(fn=cmd_leads)
    s = sub.add_parser("contacts", help="list contacts")
    s.add_argument("--stage")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(fn=cmd_contacts)
    s = sub.add_parser("write", help="enroll new leads and write due drafts now")
    s.add_argument("--limit", type=int, default=25)
    s.set_defaults(fn=cmd_write)
    s = sub.add_parser("outbox", help="review drafts: list | show ID | approve ID|0 | reject ID | skip ID | stop ID")
    s.add_argument("action", nargs="?", choices=["list", "show", "approve", "reject", "skip", "stop"])
    s.add_argument("id", nargs="?", type=int, default=0, help="message id (0 = all passing checks, approve only)")
    s.set_defaults(fn=cmd_outbox)
    s = sub.add_parser("send", help="send due approved emails now")
    s.add_argument("--now", action="store_true", help="ignore office hours and the gap (caps still apply)")
    s.add_argument("--max", type=int, default=1)
    s.set_defaults(fn=cmd_send)
    sub.add_parser("inbox", help="check the mailbox for replies now").set_defaults(fn=cmd_inbox)
    s = sub.add_parser("simulate-reply", help="test: pretend a contact replied")
    s.add_argument("contact", help="contact id or email")
    s.add_argument("text")
    s.set_defaults(fn=cmd_reply)

    s = sub.add_parser("run", help="run the 24/7 engine (no dashboard)")
    s.add_argument("--once", action="store_true", help="run a single cycle and exit")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("dashboard", help="open the web dashboard")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(fn=cmd_dashboard)
    s = sub.add_parser("start", help="engine + dashboard together (24/7)")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_start)
    s = sub.add_parser("service", help="run 24/7 in the background: install | status | stop | start | uninstall | logs")
    s.add_argument("action", nargs="?", default="status", choices=["install", "status", "stop", "start", "uninstall", "logs"])
    s.add_argument("--lines", type=int, default=40)
    s.set_defaults(fn=cmd_service)
    sub.add_parser("open", help="open the dashboard in your browser").set_defaults(fn=cmd_open)

    s = sub.add_parser("sending", help="pause / resume all sending immediately")
    s.add_argument("state", choices=["pause", "resume"])
    s.set_defaults(fn=cmd_sending)
    s = sub.add_parser("autopilot", help="turn approval-free sending on/off")
    s.add_argument("state", choices=["on", "off"])
    s.add_argument("--replies", action="store_true", help="for replies instead of outreach")
    s.set_defaults(fn=cmd_autopilot)
    s = sub.add_parser("stage", help="move a contact to a stage (starts that stage's follow-ups)")
    s.add_argument("contact", type=int)
    s.add_argument("stage")
    s.add_argument("--reason")
    s.set_defaults(fn=cmd_stage)
    s = sub.add_parser("won", help="mark a deal as WON and start client onboarding")
    s.add_argument("contact", type=int)
    s.add_argument("--value", type=float)
    s.add_argument("--currency", default="INR")
    s.set_defaults(fn=cmd_won)
    s = sub.add_parser("onboard", help="show / tick onboarding steps for a client")
    s.add_argument("contact", type=int)
    s.add_argument("step", nargs="?")
    s.add_argument("--undo", action="store_true")
    s.set_defaults(fn=cmd_onboard)
    sub.add_parser("stats", help="numbers: reply rate, bounces, meetings, per step").set_defaults(fn=cmd_stats)
    s = sub.add_parser("export", help="export contacts (or --calls: today's call list) to CSV")
    s.add_argument("--stage")
    s.add_argument("--calls", action="store_true")
    s.add_argument("--file")
    s.set_defaults(fn=cmd_export)
    s = sub.add_parser("suppress", help="never email this address or @domain")
    s.add_argument("value")
    s.set_defaults(fn=cmd_suppress)
    s = sub.add_parser("forget", help="delete everything about an email address (erasure request)")
    s.add_argument("email")
    s.set_defaults(fn=cmd_forget)
    return p


def main(argv: list[str] | None = None) -> None:
    import os
    if sys.stdout is None or sys.stderr is None:  # started without a console (pythonw / background service)
        devnull = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = sys.stdout or devnull
        sys.stderr = sys.stderr or devnull
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    args.fn(args)


if __name__ == "__main__":
    main()
