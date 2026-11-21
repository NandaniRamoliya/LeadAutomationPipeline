"""`leadpipe demo`: a safe sandbox with made-up businesses so you can explore everything (to-do tasks for
email / WhatsApp / calls, replies, follow-up tracks, the area rollout, onboarding) before connecting a mailbox.
Everything stays in ./demo/ and nothing is ever sent (test mode)."""
from __future__ import annotations

import json
import os
import shutil
from datetime import timedelta
from pathlib import Path

import yaml

from . import onboarding
from .config import DEFAULT_CONFIG_PATH, load_config
from .db import DB, iso, utcnow
from .handler import simulate_reply
from .pipeline import enroll_new_contacts
from .sender import Mailer, send_due
from .writer import write_due

# name, category, domain, person, role, signals, phone, instagram, priority, rating, reviews
BUSINESSES = [
    ("Smile Dental Care", "dentist", "smiledental-demo.test", "Dr. Priya Sharma", "Founder",
     {"no_online_booking": "no booking tool found", "not_mobile_friendly": "no viewport", "outdated_site": {"copyright_year": "© 2018"}},
     "+91 98250 11111", None, True, 4.6, 212),
    ("Bright Physio Clinic", "clinic", "brightphysio-demo.test", "Amit Rao", "Owner",
     {"no_online_booking": "no booking tool found", "slow_site": {"seconds": 6.2}}, "+91 98250 22222", None, False, 4.1, 38),
    ("The Bean Corner Cafe", "cafe", None, None, None, {"no_website": "no website listed", "instagram_only": "instagram.com"},
     "+91 98250 33333", "https://instagram.com/beancorner.demo", False, 4.4, 96),
    ("FitZone Gym", "gym", "fitzone-demo.test", "Rohan Kulkarni", "Director",
     {"no_https": "plain http", "no_contact_form": "no form", "running_ads": "Meta Pixel"}, "+91 98250 44444",
     "https://instagram.com/fitzone.demo", True, 3.8, 61),
    ("Glow Beauty Studio", "salon", "glowstudio-demo.test", "Sneha Joshi", "Owner",
     {"no_online_booking": "no booking tool found", "no_live_chat": "no chat"}, None, None, False, None, None),
    ("Kumar & Associates", "accountant", "kumarassoc-demo.test", "Vikram Kumar", "Managing Partner",
     {"weak_seo": "missing description", "not_mobile_friendly": "no viewport"}, "+91 261 222 3333", None, False, None, None),
    ("Little Steps Preschool", "school", "littlesteps-demo.test", "Anita Desai", "Principal",
     {"outdated_site": {"copyright_year": "© 2016"}, "no_contact_form": "no form"}, None, None, False, None, None),
    ("Spice Route Restaurant", "restaurant", None, "Imran Shaikh", "Owner",
     {"no_website": "no website listed", "few_reviews": {"reviews": "9"}}, "+91 98250 55555", None, True, 4.2, 9),
]

REPLIES = [
    (1, "Sounds interesting. How much would something like this cost?"),
    (2, "Not right now, we're busy until next month. Maybe after that."),
    (4, "Yes please, send the audit."),
    (5, "Please remove me from your list."),
    (6, "I've booked a slot for Thursday at 11."),
]


def make_demo(folder: str | Path = "demo") -> Path:
    from .templates import SIGNALS, fill
    root = Path(folder).resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["profile"].update({"your_name": "Asha Patel", "business_name": "Pixel Studio (demo)",
                            "website": "https://pixelstudio-demo.test", "physical_address": "12 MG Road, Pune 411001, India",
                            "signature": "Asha Patel\nPixel Studio | pixelstudio-demo.test",
                            "booking_link": "https://cal.com/asha-demo/15min"})
    data["llm"]["provider"] = "none"
    data["sending"]["mode"] = "dry_run"
    data["sending"]["warmup"]["enabled"] = False
    data["reach"]["auto_discover"] = False
    data["reach"]["start_area"] = "Surat, India"
    data["channels"]["min_score_for_tasks"] = 30
    (root / "config.yaml").write_text("# DEMO config (made-up data, nothing is sent)\n" +
                                      yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (root / ".env").write_text("FROM_EMAIL=asha@getpixelstudio-demo.test\nFROM_NAME=Asha Patel\n", encoding="utf-8")

    os.environ.pop("LEADPIPE_DATA_DIR", None)
    os.environ.pop("LEADPIPE_DB", None)
    cfg = load_config(root / "config.yaml")
    db = DB(cfg.db_path)
    from .discovery import seed_areas
    seed_areas(db, cfg)
    db.execute("UPDATE areas SET status='done', tile=9, tiles=9, found=412, timezone='Asia/Kolkata' WHERE name='Surat, India'")
    db.execute("UPDATE areas SET status='active', tile=3, tiles=9, found=96, timezone='Asia/Kolkata' WHERE name='Mumbai, India'")
    area_id = db.scalar("SELECT id FROM areas WHERE name='Surat, India'")

    for i, (name, cat, domain, person, role, signals, phone, insta, prio, rating, reviews) in enumerate(BUSINESSES, 1):
        problems = [fill(SIGNALS[k]["problem"], v if isinstance(v, dict) else {}) for k, v in signals.items()][:2]
        score = sum(float(cfg.get(f"signals.{k}", 0) or 0) for k in signals) + (10 if person else 0) + (30 if prio else 0)
        cid = db.insert("companies", {
            "name": name, "category": cat, "website": f"https://{domain}" if domain else None, "domain": domain,
            "city": "Surat", "country": "IN", "timezone": "Asia/Kolkata", "phone": phone, "instagram": insta,
            "priority": 1 if prio else 0, "rating": rating, "reviews": reviews, "area_id": area_id,
            "source": "demo", "source_ref": str(i), "signals": json.dumps(signals), "problems": json.dumps(problems),
            "score": min(score, 100), "status": "qualified", "status_reason": f"score {score}"})
        first = None
        if person:
            first = person.replace("Dr. ", "Dr ").split()[0]
            if first == "Dr":
                first = "Dr " + person.split()[-1]
        if domain:
            db.insert("contacts", {"company_id": cid, "full_name": person, "first_name": first, "role": role,
                                   "email": f"{person.replace('Dr. ', '').split()[0].lower()}@{domain}", "email_status": "valid",
                                   "email_source": "demo", "channel": "email", "stage": "new"})
        else:
            db.insert("contacts", {"company_id": cid, "full_name": person, "first_name": first, "role": role,
                                   "channel": "social", "stage": "new", "email_status": "none"})

    db.execute("DELETE FROM kv WHERE key LIKE 'enrolled:%'")
    cfg.data["sending"]["new_leads_per_day"] = 20
    enroll_new_contacts(db, cfg)
    write_due(db, cfg)
    db.execute("UPDATE messages SET status='approved' WHERE status='pending' AND channel='email'")
    send_due(db, cfg, Mailer(cfg), respect_timing=False, max_send=20)
    for cid, text in REPLIES:
        simulate_reply(db, cfg, cid, text)
    simulate_reply(db, cfg, 7, "Let's go ahead. Please send the agreement.")
    onboarding.start(db, cfg, 7, value=15000, currency="INR")
    # Move FitZone (high-value, email) a few days on so its call task shows up today.
    fit = db.one("SELECT * FROM contacts WHERE id = 4")
    if fit and fit["stage"] == "interested":
        db.update("contacts", 4, {"next_action_at": iso(utcnow() - timedelta(hours=1)), "step": 1})
    db.execute("UPDATE contacts SET next_action_at = ? WHERE channel='social'", (iso(utcnow() - timedelta(minutes=5)),))
    write_due(db, cfg)
    db.close()
    return root
