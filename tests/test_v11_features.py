"""Channels, world rollout, Google-Maps-style imports, email patterns, multiple mailboxes, call/DM tasks,
the background service files, and the settings writer."""
import json
from datetime import timedelta

import yaml

from leadpipe import channels as chn
from leadpipe import service, tasks
from leadpipe.config import load_config, save_env, save_settings
from leadpipe.db import iso, utcnow
from leadpipe.discovery import add_area, current_area, run_discovery, seed_areas, tile_bbox
from leadpipe.discovery.csv_import import import_csv, map_headers, sheet_csv_url
from leadpipe.enrich import enrich_company
from leadpipe.enrich.patterns import apply_pattern, learn_pattern
from leadpipe.enrich.website import SiteAudit
from leadpipe.geo import timezone_for
from leadpipe.pipeline import enroll_new_contacts
from leadpipe.sender import Mailer, send_due
from leadpipe.writer import write_due

from .test_end_to_end import approve_all, send_all, setup_leads, time_travel


# ---- channels ---------------------------------------------------------------------
def test_phone_and_social_parsing():
    p = chn.parse_phone("+91 98250 12345")
    assert p["e164"] == "+919825012345" and p["mobile"]
    assert chn.parse_phone("98250 12345", "IN")["e164"] == "+919825012345"
    assert chn.parse_phone("020 7946 0958", "GB")["mobile"] is False
    assert chn.parse_phone("hello") is None
    s = chn.extract_socials(["https://www.instagram.com/smile.dental/", "https://facebook.com/sharer.php?u=x",
                             "https://www.linkedin.com/company/smile-dental", "https://wa.me/919825012345",
                             "https://twitter.com/intent/tweet", "https://x.com/smiledental"])
    assert s["instagram"] == "https://instagram.com/smile.dental"
    assert s["linkedin"].endswith("linkedin.com/company/smile-dental")
    assert s["whatsapp"] == "919825012345" and s["twitter"] == "https://x.com/smiledental"
    assert "facebook" not in s


def test_channel_choice_and_links(cfg):
    company = {"phone": "+91 98250 12345", "country": "IN", "instagram": "smile.dental"}
    avail = chn.available(company, {}, cfg)
    assert set(avail) == {"whatsapp", "instagram", "call"}
    assert chn.pick_channel("email", avail, cfg) == "whatsapp"          # no email -> best DM app
    assert chn.pick_channel("dm", {"call": "+1"}, cfg) == "call"         # no DM app -> call
    assert chn.pick_channel("call", {"email": "a@b.c"}, cfg) is None    # nothing to call
    url = chn.action_url("whatsapp", "919825012345", "Hi there! 1 & 2")
    assert url == "https://wa.me/919825012345?text=Hi%20there%21%201%20%26%202"
    assert chn.action_url("instagram", "https://instagram.com/smile.dental") == "https://ig.me/m/smile.dental"


# ---- world rollout ---------------------------------------------------------------------
def test_area_rollout_order_and_progress(cfg, db, monkeypatch):
    seed_areas(db, cfg)
    names = [a["name"] for a in db.all("SELECT name FROM areas ORDER BY position")]
    assert names[0] == "Pune, India"
    assert "Pune, India" not in names[1:]                    # start city not repeated in the country list
    assert names[1] == "Mumbai, India"                        # then the rest of India
    india = [n for n in names if n.endswith("India")]
    assert names.index("Dubai, United Arab Emirates") > names.index(india[-1])   # then the next countries
    assert seed_areas(db, cfg) == 0                           # idempotent

    from leadpipe.discovery import osm
    monkeypatch.setattr(osm, "locate_area", lambda name: {"lat": 18.52, "lon": 73.85, "bbox": (18.4, 73.7, 18.6, 74.0),
                                                          "country": "IN", "display_name": name})
    calls = []

    def fake_tile(bbox, cats, limit, label):
        calls.append(bbox)
        i = len(calls)
        return [{"name": f"Clinic {i}", "category": "dentist", "website": f"https://clinic{i}.test",
                 "phone": "+91 98250 0000" + str(i), "source": "osm", "source_ref": f"node/{i}", "lat": 18.5, "lon": 73.8}]
    monkeypatch.setattr(osm, "search_tile", fake_tile)
    total = run_discovery(db, cfg, steps=9)
    pune = db.one("SELECT * FROM areas WHERE name='Pune, India'")
    assert total == 9 and pune["status"] == "done" and pune["tiles"] == 9 and pune["timezone"] == "Asia/Kolkata"
    assert len({c for c in calls}) == 9                       # nine different pieces of the map
    assert current_area(db)["name"] == "Mumbai, India"        # moved on by itself
    co = db.one("SELECT * FROM companies LIMIT 1")
    assert co["area_id"] == pune["id"] and co["timezone"] == "Asia/Kolkata"
    add_area(db, "Ahmedabad, India")
    db.execute("UPDATE areas SET status='pending' WHERE status='active'")
    assert current_area(db)["name"] == "Ahmedabad, India"


def test_tiles_and_timezones():
    tiles = [tile_bbox((0, 0, 3, 3), 9, i) for i in range(9)]
    assert tiles[0] == (0, 0, 1, 1) and tiles[8] == (2, 2, 3, 3)
    assert timezone_for("IN") == "Asia/Kolkata"
    assert timezone_for("US", 40.7, -74.0) == "America/New_York"
    assert timezone_for("US", 34.05, -118.24) == "America/Los_Angeles"
    assert timezone_for("AE") == "Asia/Dubai"


# ---- imports -------------------------------------------------------------------------------
MAPS_CSV = """title,phone,website,address,totalScore,reviewsCount,categoryName,instagram,url,priority
Spice Route Restaurant,+91 98250 55555,,"12 Ring Road, Surat, Gujarat 395002",4.2,9,Restaurant,,https://maps.google.com/?cid=1,yes
Glow Studio,098250 66666,glowstudio.test,"Adajan, Surat",3.6,41,Beauty salon,https://instagram.com/glow.studio,https://maps.google.com/?cid=2,
"""


def test_maps_scraper_import_and_social_track(cfg, db):
    m = map_headers(["title", "phone", "website", "totalScore", "reviewsCount", "categoryName", "url"])
    assert m["company"] == "title" and m["rating"] == "totalScore" and m["reviews"] == "reviewsCount"
    assert m["category"] == "categoryName" and m["maps_url"] == "url"
    st = import_csv(db, cfg, text=MAPS_CSV, area="Surat, India")
    assert st["companies"] == 2
    spice = db.one("SELECT * FROM companies WHERE name='Spice Route Restaurant'")
    assert spice["priority"] == 1 and spice["rating"] == 4.2 and spice["reviews"] == 9 and spice["phone"] == "+919825055555"
    assert spice["timezone"] == "Asia/Kolkata" and spice["city"] == "Surat"
    assert import_csv(db, cfg, text=MAPS_CSV)["companies"] == 0          # re-import is safe
    co = enrich_company(db, cfg, spice, audit=SiteAudit(url=None, signals={"no_website": "no website listed"}))
    assert co["status"] == "qualified" and "few_reviews" in json.loads(co["signals"])
    social = db.one("SELECT * FROM contacts WHERE company_id=?", (co["id"],))
    assert social["channel"] == "social" and social["email"] is None
    enroll_new_contacts(db, cfg)
    assert db.contact(social["id"])["sequence"] == "cold_social"
    write_due(db, cfg)
    task = db.one("SELECT * FROM messages WHERE contact_id=?", (social["id"],))
    assert task["channel"] == "whatsapp" and task["status"] == "pending" and task["purpose"] == "dm_intro"
    assert task["action_url"].startswith("https://wa.me/919825055555?text=")
    assert "[" not in task["body"] and "{" not in task["body"]


def test_sheet_links():
    url = "https://docs.google.com/spreadsheets/d/1AbC_d-9/edit?gid=1662993032#gid=1662993032"
    assert sheet_csv_url(url) == "https://docs.google.com/spreadsheets/d/1AbC_d-9/export?format=csv&gid=1662993032"
    assert sheet_csv_url("https://example.com/x.csv") is None


# ---- email patterns --------------------------------------------------------------------------
def test_email_pattern_learning():
    people = [{"full_name": "Nora Vale", "role": "Office Manager"}, {"full_name": "Dr. Ravi Mehta", "role": "Founder"}]
    pat = learn_pattern(["nvale@clinic.test", "info@clinic.test"], people, "clinic.test")
    assert pat == "flast"
    assert apply_pattern(pat, people[1], people, "clinic.test") == "rmehta@clinic.test"
    twins = [{"full_name": "John Smith"}, {"full_name": "Jane Smith"}]
    assert apply_pattern("flast", twins[0], twins, "x.test") is None        # ambiguous: never guess
    assert learn_pattern(["kmorrow@x.test"], [{"full_name": "Ann Lee"}], "x.test") is None


def test_pattern_contact_created_for_owner(cfg, db):
    cid = db.insert("companies", {"name": "Vale Dental", "website": "https://clinic.test", "domain": "clinic.test",
                                  "status": "new", "country": "IN"})
    audit = SiteAudit(url="https://clinic.test", ok=True, signals={"no_online_booking": "x", "not_mobile_friendly": "y"},
                      emails=["nvale@clinic.test", "info@clinic.test"],
                      people=[{"full_name": "Nora Vale", "role": "Office Manager"}, {"full_name": "Ravi Mehta", "role": "Founder"}])
    enrich_company(db, cfg, db.company(cid), audit=audit)
    emails = {c["email"]: c for c in db.all("SELECT * FROM contacts WHERE company_id=?", (cid,))}
    assert "rmehta@clinic.test" in emails and emails["rmehta@clinic.test"]["email_status"] == "pattern"
    assert emails["rmehta@clinic.test"]["first_name"] == "Ravi"


# ---- several mailboxes ------------------------------------------------------------------------
def test_multiple_mailboxes_share_the_load(cfg, db, monkeypatch):
    monkeypatch.setenv("MB2_PASS", "secret")
    cfg.data["mailboxes"] = [{"name": "second", "email": "asha@pixelstudio-mail.test", "smtp_host": "smtp.test",
                              "password_env": "MB2_PASS"}]
    setup_leads(db, cfg)
    enroll_new_contacts(db, cfg)
    write_due(db, cfg)
    approve_all(db)
    sent = send_due(db, cfg, Mailer(cfg), respect_timing=False, max_send=1)
    assert sent == 2                                          # one from each mailbox
    boxes = {r["mailbox"] for r in db.all("SELECT mailbox FROM messages WHERE status='sent'")}
    assert boxes == {"main", "second"}
    owner = {c["email"]: c["mailbox"] for c in db.all("SELECT email, mailbox FROM contacts WHERE mailbox IS NOT NULL")}
    # Follow-ups go from the same mailbox
    time_travel(db, 3)
    write_due(db, cfg)
    approve_all(db)
    send_due(db, cfg, Mailer(cfg), respect_timing=False, max_send=5)
    for r in db.all("""SELECT ct.email, m.mailbox FROM messages m JOIN contacts ct ON ct.id = m.contact_id
                       WHERE m.status='sent' AND m.step = 1"""):
        assert owner[r["email"]] == r["mailbox"]
    eml = [p.read_text() for p in (cfg.data_dir / "dry_run").glob("*.eml")]
    assert any("asha@pixelstudio-mail.test" in t for t in eml) and any("asha@getpixelstudio.test" in t for t in eml)


# ---- call and DM tasks ---------------------------------------------------------------------------
def test_call_task_outcomes(cfg, db):
    setup_leads(db, cfg)
    db.execute("UPDATE companies SET phone='+91 98250 12345', score=80 WHERE name='Bright Clinic'")
    enroll_new_contacts(db, cfg)
    amit = db.one("SELECT * FROM contacts WHERE email='amit@brightclinic.test'")
    for _ in range(3):                                         # intro, bump, then the call step (day 5)
        write_due(db, cfg)
        approve_all(db)
        send_all(db, cfg)
        time_travel(db, 3)
    write_due(db, cfg)
    t = tasks.todo(db, cfg, horizon_hours=48)
    call = next(m for m in t["calls"] if m["contact_id"] == amit["id"])
    assert call["to_addr"] == "+919825012345" and "OPENER" in call["body"]
    msg = tasks.call_outcome(db, cfg, call["id"], "no_answer")
    assert "tomorrow" in msg and db.message(call["id"])["status"] == "approved"
    tasks.call_outcome(db, cfg, call["id"], "no_answer")       # 2nd attempt: give up, move on
    assert db.message(call["id"])["status"] == "sent" and db.contact(amit["id"])["step"] == 3


def test_call_answered_interested_moves_lead(cfg, db):
    setup_leads(db, cfg)
    db.execute("UPDATE companies SET phone='+91 98250 12345' WHERE name='Bright Clinic'")
    enroll_new_contacts(db, cfg)
    amit = db.one("SELECT * FROM contacts WHERE email='amit@brightclinic.test'")
    from leadpipe.templates import Draft
    from leadpipe.writer import queue_message
    mid = queue_message(db, cfg, amit, db.company(amit["company_id"]), Draft("Call", "script"), "sequence",
                        sequence="cold", step=0, purpose="call_intro", channel="call", target="+919825012345")
    tasks.call_outcome(db, cfg, mid, "interested", notes="wants a demo next week")
    assert db.contact(amit["id"])["stage"] == "interested"
    assert db.one("SELECT 1 FROM messages WHERE contact_id=? AND kind='reply'", (amit["id"],))


def test_dm_done_and_logged_reply(cfg, db):
    import_csv(db, cfg, text=MAPS_CSV, area="Surat, India")
    spice = db.one("SELECT * FROM companies WHERE name='Spice Route Restaurant'")
    enrich_company(db, cfg, spice, audit=SiteAudit(url=None, signals={"no_website": "x"}))
    enroll_new_contacts(db, cfg)
    write_due(db, cfg)
    task = db.one("SELECT * FROM messages WHERE channel='whatsapp'")
    tasks.done(db, cfg, task["id"])
    c = db.contact(task["contact_id"])
    assert c["step"] == 1 and db.message(task["id"])["status"] == "sent"
    r = tasks.log_reply(db, cfg, c["id"], "Yes interested, how much?", channel="whatsapp")
    assert r["intent"] == "price_question" and db.contact(c["id"])["stage"] == "interested"
    reply = db.one("SELECT * FROM messages WHERE contact_id=? AND kind='reply'", (c["id"],))
    assert reply["channel"] == "whatsapp" and reply["action_url"].startswith("https://wa.me/")
    # They opt out on WhatsApp -> their number is never used again
    tasks.log_reply(db, cfg, c["id"], "stop messaging me", channel="whatsapp")
    from leadpipe.pipeline import is_suppressed
    assert is_suppressed(db, "919825055555") or is_suppressed(db, "+919825055555")


def test_task_quota_and_min_score(cfg, db):
    cfg.data["channels"]["tasks_per_day"] = 1
    import_csv(db, cfg, text=MAPS_CSV.replace(",yes\n", ",\n"), area="Surat, India")
    for co in db.all("SELECT * FROM companies"):
        enrich_company(db, cfg, co, audit=SiteAudit(url=None, signals={"no_website": "x", "instagram_only": "y"}))
    enroll_new_contacts(db, cfg)
    write_due(db, cfg)
    assert db.scalar("SELECT COUNT(*) FROM messages WHERE channel != 'email'") == 1   # capped at 1 task today
    later = db.one("SELECT * FROM contacts WHERE channel='social' AND id NOT IN (SELECT contact_id FROM messages)")
    assert later and later["next_action_at"] > iso(utcnow() + timedelta(hours=10))   # postponed to tomorrow


# ---- running 24/7 ---------------------------------------------------------------------------------
def test_service_definitions(cfg):
    xml = service.windows_task_xml(cfg)
    for must in ("<LogonTrigger>", "PT5M", "<WakeToRun>true</WakeToRun>", "IgnoreNew", "<ExecutionTimeLimit>PT0S",
                 "<DisallowStartIfOnBatteries>false", "start", "--background", "leadpipe"):
        assert must in xml, must
    plist = service.mac_plist(cfg)
    assert "<key>KeepAlive</key><true/>" in plist and "<key>RunAtLoad</key><true/>" in plist
    unit = service.systemd_unit(cfg)
    assert "Restart=always" in unit and "--background" in unit
    st = service.status(cfg)
    assert st["running"] is False


# ---- settings writer --------------------------------------------------------------------------------
def test_settings_writer_keeps_comments(tmp_path, monkeypatch):
    from leadpipe.config import DEFAULT_CONFIG_PATH
    monkeypatch.delenv("FROM_EMAIL", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    cfg = load_config(path)
    save_settings(cfg, {"profile.your_name": "Tushar", "reach.start_area": "Surat, India",
                        "reach.business_types": ["cafe", "gym"], "channels.tasks_per_day": 30})
    text = path.read_text(encoding="utf-8")
    assert "# 1. WHO YOU ARE" in text and "your_name: \"Tushar\"" in text
    assert cfg.get("reach.business_types") == ["cafe", "gym"] and cfg.get("channels.tasks_per_day") == 30
    save_env(cfg, {"SMTP_HOST": "smtp.gmail.com", "SMTP_PASSWORD": "abc def"})
    save_env(cfg, {"SMTP_HOST": "smtp.office365.com", "SMTP_PASSWORD": ""})
    env = (tmp_path / ".env").read_text()
    assert "SMTP_HOST=smtp.office365.com" in env and 'SMTP_PASSWORD="abc def"' in env


def test_old_config_is_upgraded(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"targets": [{"name": "a", "area": "Pune, India", "categories": ["dentist"]},
                                                {"name": "b", "area": "Dubai, UAE", "categories": ["cafe"]}],
                                    "discovery": {"providers": ["osm", "google_places"]}}))
    cfg = load_config(path)
    assert cfg.get("reach.start_area") == "Pune, India" and cfg.get("reach.next_areas") == ["Dubai, UAE"]
    assert cfg.get("reach.business_types") == ["dentist", "cafe"] and cfg.get("sources.providers") == ["osm", "google_places"]


def test_dashboard_setup_form_saves(cfg, db, tmp_path):
    import re

    from leadpipe.dashboard.app import create_app
    from leadpipe.config import DEFAULT_CONFIG_PATH
    cfg.config_path.write_text(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    cfg.reload_if_changed()
    app = create_app(cfg, db)
    app.testing = True
    c = app.test_client()
    token = re.search(r'name="_csrf" value="([0-9a-f]+)"', c.get("/settings").get_data(as_text=True)).group(1)
    r = c.post("/settings/setup", data={"_csrf": token, "your_name": "Tushar", "business_name": "TD Studio",
                                        "business_types": "cafe, gym", "start_area": "Surat, India", "world_rollout": "1",
                                        "countries": "AE, GB", "channels": ["email", "whatsapp", "call"],
                                        "tasks_per_day": "20", "min_score_for_tasks": "40", "daily_cap": "30",
                                        "new_leads_per_day": "10", "FROM_EMAIL": "t@getstudio.test", "SMTP_HOST": "smtp.gmail.com",
                                        "SMTP_PASSWORD": "pw", "timezone": "Asia/Kolkata"})
    assert r.status_code == 302
    assert cfg.get("profile.your_name") == "Tushar" and cfg.get("reach.business_types") == ["cafe", "gym"]
    assert cfg.get("channels.enabled") == ["email", "whatsapp", "call"] and cfg.get("sending.mode") == "dry_run"
    assert "SMTP_PASSWORD=pw" in cfg.env_path.read_text()
    assert db.scalar("SELECT COUNT(*) FROM areas WHERE name='Surat, India'") == 1
