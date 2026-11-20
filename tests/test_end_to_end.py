"""Full journey: lead -> cold sequence -> reply -> warm -> meeting -> proposal -> WON -> onboarding -> client care.
Plus the 'no reply' and 'not now' branches. Runs in dry_run mode (emails are written to .eml files)."""
from datetime import datetime, timedelta, timezone
from email import message_from_bytes

from leadpipe import onboarding
from leadpipe.discovery.csv_import import import_csv
from leadpipe.db import iso, jloads, utcnow
from leadpipe.enrich import enrich_company
from leadpipe.enrich.website import SiteAudit
from leadpipe.handler import simulate_reply
from leadpipe.pipeline import enroll_new_contacts, is_suppressed, process_timers
from leadpipe.sender import Mailer, in_office_hours, send_due, todays_cap
from leadpipe.writer import write_due

CSV = """Company,Website,Name,Title,Email,City,Category
Smile Dental Care,smiledental.test,Priya Sharma,Founder,priya@smiledental.test,Pune,dentist
Smile Dental Care,smiledental.test,Rahul Mehta,Practice Manager,rahul@smiledental.test,Pune,dentist
Bright Clinic,brightclinic.test,Amit Rao,Owner,amit@brightclinic.test,Pune,clinic
"""

# A Tuesday, 11:00 in Pune: inside office hours.
TUE_11 = datetime(2026, 11, 17, 5, 30, tzinfo=timezone.utc)


def fake_audit(url):
    return SiteAudit(url=url, ok=True, signals={"no_online_booking": "no booking tool found",
                                               "not_mobile_friendly": "no viewport"}, summary="Dental clinic")


def setup_leads(db, cfg):
    stats = import_csv(db, cfg, text=CSV, target="pune-dentists")
    assert stats["companies"] == 2 and stats["contacts"] == 3
    for co in db.all("SELECT * FROM companies"):
        enrich_company(db, cfg, co, audit=fake_audit(co["website"]))
    assert db.scalar("SELECT COUNT(*) FROM companies WHERE status='qualified'") == 2


def approve_all(db):
    db.execute("UPDATE messages SET status='approved' WHERE status='pending' AND direction='out'")


def time_travel(db, days):
    """Make every scheduled action `days` earlier, as if time passed."""
    for c in db.all("SELECT id, next_action_at FROM contacts WHERE next_action_at IS NOT NULL"):
        db.update("contacts", c["id"], {"next_action_at": iso(datetime.fromisoformat(c["next_action_at"]) - timedelta(days=days))})
    db.execute("UPDATE messages SET scheduled_for = ? WHERE status IN ('pending','approved')", (iso(utcnow() - timedelta(minutes=1)),))
    for m in db.all("SELECT id, sent_at FROM messages WHERE sent_at IS NOT NULL"):
        db.update("messages", m["id"], {"sent_at": iso(datetime.fromisoformat(m["sent_at"]) - timedelta(days=days))})
    db.execute("DELETE FROM kv WHERE key LIKE 'enrolled:%' OR key = 'next_send_at'")


def send_all(db, cfg, max_send=10):
    return send_due(db, cfg, Mailer(cfg), respect_timing=False, max_send=max_send)


def test_full_journey_to_client(cfg, db):
    setup_leads(db, cfg)
    assert enroll_new_contacts(db, cfg) == 3
    assert write_due(db, cfg) == 3
    drafts = db.all("SELECT * FROM messages WHERE status='pending'")
    assert all(d["purpose"] == "intro" and d["generator"] == "template" for d in drafts)
    assert all(not any(i["level"] == "block" for i in jloads(d["check_issues"], [])) for d in drafts), drafts

    # Nothing goes out without approval.
    assert send_all(db, cfg) == 0
    approve_all(db)
    sent = send_all(db, cfg)
    # one_company_per_day: only one of the two Smile Dental contacts today + Bright Clinic
    assert sent == 2
    files = list((cfg.data_dir / "dry_run").glob("*.eml"))
    assert len(files) == 2
    em = message_from_bytes(files[0].read_bytes())
    assert em["List-Unsubscribe"] and "mailto:" in em["List-Unsubscribe"]
    body = em.get_payload(decode=True).decode()
    assert "12 MG Road" in body and "no thanks" in body and "Asha Patel" in body

    priya = db.one("SELECT * FROM contacts WHERE email='priya@smiledental.test'")
    rahul = db.one("SELECT * FROM contacts WHERE email='rahul@smiledental.test'")
    first_sent = priya if priya["step"] == 1 else rahul
    assert first_sent["step"] == 1 and first_sent["next_action_at"]

    # Day 3: follow-up "bump" is written in the same thread.
    time_travel(db, 3)
    write_due(db, cfg)
    bump = db.one("SELECT * FROM messages WHERE contact_id=? AND step=1", (first_sent["id"],))
    assert bump["purpose"] == "bump" and bump["subject"].startswith("Re: ")
    approve_all(db)
    send_all(db, cfg)
    bump = db.message(bump["id"])
    assert bump["status"] == "sent"

    # They reply with a price question -> interested, warm track, Closer reply drafted with the price range.
    r = simulate_reply(db, cfg, first_sent["id"], "Sounds good. How much would something like this cost?")
    assert r["intent"] == "price_question"
    c = db.contact(first_sent["id"])
    assert c["stage"] == "interested" and c["sequence"] == "warm"
    other = rahul if first_sent is priya else priya
    assert db.contact(other["id"])["stage"] == "paused"  # colleague's cold emails stopped
    reply = db.one("SELECT * FROM messages WHERE contact_id=? AND kind='reply'", (c["id"],))
    assert reply["status"] == "pending" and "₹8,000" in reply["body"] and "cal.com/asha" in reply["body"]
    assert reply["in_reply_to"].startswith("<sim-")
    approve_all(db)
    assert send_all(db, cfg) >= 1
    sent_mails = [message_from_bytes(p.read_bytes()) for p in (cfg.data_dir / "dry_run").glob("*.eml")]
    threaded = [m for m in sent_mails if (m["In-Reply-To"] or "").startswith("<sim-")]
    assert threaded and threaded[0]["Subject"].startswith("Re: ") and threaded[0]["References"]
    assert "List-Unsubscribe" not in threaded[0]  # replies to a human are not marketing

    # They book a call -> meeting; after the call date the proposal track starts by itself.
    simulate_reply(db, cfg, c["id"], "Great, I've booked a slot for Thursday.")
    assert db.contact(c["id"])["stage"] == "meeting"
    time_travel(db, 2)
    process_timers(db, cfg)
    c = db.contact(c["id"])
    assert c["stage"] == "proposal" and c["sequence"] == "proposal"

    # "Let's go ahead" -> flagged for you; you mark WON -> onboarding starts with the contract email.
    r = simulate_reply(db, cfg, c["id"], "Let's go ahead. Please send the agreement.")
    assert r["intent"] == "ready_to_buy" and r["needs_human"]
    onboarding.start(db, cfg, c["id"], value=12000, currency="INR")
    assert db.contact(c["id"])["stage"] == "won"
    contract = db.one("SELECT * FROM messages WHERE contact_id=? AND kind='onboarding' AND purpose='contract'", (c["id"],))
    assert contract and "agreement" in contract["body"].lower()

    approve_all(db)
    send_all(db, cfg)
    onboarding.tick(db, cfg)
    deal = db.one("SELECT * FROM deals WHERE contact_id=?", (c["id"],))
    assert "contract_sent" in jloads(deal["checklist"], {})

    # Reminder when the contract isn't signed after 2 days
    time_travel(db, 3)
    onboarding.tick(db, cfg)
    assert db.one("SELECT 1 FROM messages WHERE contact_id=? AND purpose='contract_reminder'", (c["id"],))

    for step_key, next_purpose in [("contract_signed", "invoice"), ("payment_received", "welcome"),
                                   ("kickoff_done", "access_request"), ("access_received", None)]:
        approve_all(db)
        send_all(db, cfg)
        onboarding.set_step(db, cfg, c["id"], step_key)
        if next_purpose:
            assert db.one("SELECT 1 FROM messages WHERE contact_id=? AND purpose=?", (c["id"], next_purpose)), next_purpose
    approve_all(db)
    send_all(db, cfg)
    onboarding.set_step(db, cfg, c["id"], "portal_live")
    assert db.one("SELECT 1 FROM messages WHERE contact_id=? AND purpose='portal_live'", (c["id"],))
    approve_all(db)
    send_all(db, cfg)
    onboarding.tick(db, cfg)
    onboarding.tick(db, cfg)
    c = db.contact(c["id"])
    assert c["stage"] == "client" and c["sequence"] == "client_care"
    assert db.one("SELECT status FROM deals WHERE contact_id=?", (c["id"],))["status"] == "active"


def test_no_reply_closes_as_lost(cfg, db):
    setup_leads(db, cfg)
    enroll_new_contacts(db, cfg)
    amit = db.one("SELECT * FROM contacts WHERE email='amit@brightclinic.test'")
    purposes = []
    for _ in range(5):
        write_due(db, cfg)
        m = db.one("SELECT * FROM messages WHERE contact_id=? AND status='pending' ORDER BY id DESC LIMIT 1", (amit["id"],))
        purposes.append(m["purpose"])
        approve_all(db)
        send_all(db, cfg)
        time_travel(db, 7)
    assert purposes == ["intro", "bump", "insight", "value", "breakup"]
    process_timers(db, cfg)
    amit = db.contact(amit["id"])
    assert amit["stage"] == "lost" and "no response" in amit["lost_reason"]


def test_not_now_goes_to_nurture_and_unsubscribe_stops_everything(cfg, db):
    setup_leads(db, cfg)
    enroll_new_contacts(db, cfg)
    write_due(db, cfg)
    approve_all(db)
    send_all(db, cfg)
    amit = db.one("SELECT * FROM contacts WHERE email='amit@brightclinic.test'")
    simulate_reply(db, cfg, amit["id"], "Not right now, maybe next month.")
    amit = db.contact(amit["id"])
    assert amit["stage"] == "nurture" and amit["sequence"] == "nurture"
    due = datetime.fromisoformat(amit["next_action_at"])
    assert timedelta(days=29) < due - utcnow() < timedelta(days=31)

    simulate_reply(db, cfg, amit["id"], "Please stop emailing me.")
    amit = db.contact(amit["id"])
    assert amit["stage"] == "unsubscribed" and is_suppressed(db, amit["email"])
    assert not db.one("SELECT 1 FROM messages WHERE contact_id=? AND status IN ('pending','approved')", (amit["id"],))


def test_sender_guards(cfg, db):
    # Office hours: Tuesday 11:00 Pune yes, Sunday no, 22:00 no.
    assert in_office_hours(cfg, "Asia/Kolkata", TUE_11)
    assert not in_office_hours(cfg, "Asia/Kolkata", TUE_11 + timedelta(days=5))
    assert not in_office_hours(cfg, "Asia/Kolkata", TUE_11 + timedelta(hours=11))
    # Warm-up: day 0 = 5, day 10 = 25, capped at daily_cap 40.
    db.kv_set("first_send_date", (TUE_11.date() - timedelta(days=10)).isoformat())
    assert todays_cap(db, cfg, TUE_11) == 25
    db.kv_set("first_send_date", (TUE_11.date() - timedelta(days=100)).isoformat())
    assert todays_cap(db, cfg, TUE_11) == 40
    # Paused switch stops everything.
    setup_leads(db, cfg)
    enroll_new_contacts(db, cfg)
    write_due(db, cfg)
    approve_all(db)
    db.kv_set("sending_paused", True)
    assert send_all(db, cfg) == 0
    db.kv_set("sending_paused", False)
    assert send_all(db, cfg) > 0
    # A suppressed address is cancelled at send time, even if approved.
    db.execute("INSERT INTO suppression (value, reason) VALUES ('@brightclinic.test', 'test')")
    time_travel(db, 3)
    write_due(db, cfg)
    approve_all(db)
    send_all(db, cfg)
    assert not db.one("""SELECT 1 FROM messages WHERE to_addr='amit@brightclinic.test' AND status='sent' AND step=1""")


def test_not_interested_gets_one_courtesy_reply_then_silence(cfg, db):
    setup_leads(db, cfg)
    enroll_new_contacts(db, cfg)
    write_due(db, cfg)
    approve_all(db)
    send_all(db, cfg)
    amit = db.one("SELECT * FROM contacts WHERE email='amit@brightclinic.test'")
    simulate_reply(db, cfg, amit["id"], "Not interested, thanks.")
    amit = db.contact(amit["id"])
    assert amit["stage"] == "lost" and is_suppressed(db, amit["email"])
    courtesy = db.one("SELECT * FROM messages WHERE contact_id=? AND kind='reply' AND status='pending'", (amit["id"],))
    assert courtesy and "won't follow up" in courtesy["body"]
    approve_all(db)
    send_all(db, cfg)
    assert db.message(courtesy["id"])["status"] == "sent"
    # Nothing further is ever written for them.
    time_travel(db, 30)
    write_due(db, cfg)
    process_timers(db, cfg)
    assert not db.one("SELECT 1 FROM messages WHERE contact_id=? AND status IN ('pending','approved')", (amit["id"],))
