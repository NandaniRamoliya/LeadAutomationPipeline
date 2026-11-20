"""`leadpipe demo`: a safe sandbox with made-up businesses so you can explore the whole pipeline
(drafts, replies, follow-up tracks, onboarding) in the dashboard before connecting a real mailbox.
Everything stays in ./demo/ and nothing is ever sent (dry_run)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from . import onboarding
from .config import DEFAULT_CONFIG_PATH, load_config
from .db import DB
from .handler import simulate_reply
from .pipeline import enroll_new_contacts
from .sender import Mailer, send_due
from .writer import write_due

BUSINESSES = [
    ("Smile Dental Care", "dentist", "smiledental-demo.test", "Dr. Priya Sharma", "Founder",
     {"no_online_booking": "no booking tool found", "not_mobile_friendly": "no viewport", "outdated_site": {"copyright_year": "© 2018"}}),
    ("Bright Physio Clinic", "clinic", "brightphysio-demo.test", "Amit Rao", "Owner",
     {"no_online_booking": "no booking tool found", "slow_site": {"seconds": 6.2}}),
    ("The Bean Corner Cafe", "cafe", None, None, None, {"no_website": "no website listed", "instagram_only": "instagram.com"}),
    ("FitZone Gym", "gym", "fitzone-demo.test", "Rohan Kulkarni", "Director",
     {"no_https": "plain http", "no_contact_form": "no form", "running_ads": "Meta Pixel"}),
    ("Glow Beauty Studio", "salon", "glowstudio-demo.test", "Sneha Joshi", "Owner",
     {"no_online_booking": "no booking tool found", "no_live_chat": "no chat"}),
    ("Kumar & Associates", "accountant", "kumarassoc-demo.test", "Vikram Kumar", "Managing Partner",
     {"weak_seo": "missing description", "not_mobile_friendly": "no viewport"}),
    ("Little Steps Preschool", "school", "littlesteps-demo.test", "Anita Desai", "Principal",
     {"outdated_site": {"copyright_year": "© 2016"}, "no_contact_form": "no form"}),
]

REPLIES = [
    (1, "Sounds interesting. How much would something like this cost?"),
    (2, "Not right now, we're busy until next month. Maybe after that."),
    (4, "Yes please, send the audit."),
    (5, "Please remove me from your list."),
    (6, "I've booked a slot for Thursday at 11."),
]


def make_demo(folder: str | Path = "demo") -> Path:
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
    data["discovery"]["enabled"] = False
    data["sending"]["warmup"]["enabled"] = False
    (root / "config.yaml").write_text("# DEMO config (made-up data, nothing is sent)\n" +
                                      yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (root / ".env").write_text("FROM_EMAIL=asha@getpixelstudio-demo.test\nFROM_NAME=Asha Patel\n", encoding="utf-8")

    import os
    os.environ.pop("LEADPIPE_DATA_DIR", None)
    os.environ.pop("LEADPIPE_DB", None)
    cfg = load_config(root / "config.yaml")
    db = DB(cfg.db_path)
    for i, (name, cat, domain, person, role, signals) in enumerate(BUSINESSES, 1):
        from .templates import SIGNALS
        problems = [SIGNALS[k]["problem"] for k in signals][:2]
        score = sum(float(cfg.get(f"signals.{k}", 0) or 0) for k in signals) + (10 if person else 0)
        cid = db.insert("companies", {
            "name": name, "category": cat, "website": f"https://{domain}" if domain else None, "domain": domain,
            "city": "Pune", "country": "IN", "timezone": "Asia/Kolkata", "phone": f"+91 98220 0{i:04d}",
            "instagram": "https://instagram.com/" + name.lower().replace(" ", "") if not domain else None,
            "source": "demo", "source_ref": str(i), "signals": json.dumps(signals), "problems": json.dumps(problems),
            "score": score, "status": "qualified", "status_reason": f"score {score}"})
        if domain:
            first = person.replace("Dr. ", "Dr ").split()[0] if person else None
            if first == "Dr":
                first = "Dr " + person.split()[-1]
            db.insert("contacts", {"company_id": cid, "full_name": person, "first_name": first, "role": role,
                                   "email": f"{person.replace('Dr. ', '').split()[0].lower()}@{domain}", "email_status": "valid",
                                   "email_source": "demo", "channel": "email", "stage": "new"})
        else:
            db.insert("contacts", {"company_id": cid, "channel": "manual", "stage": "new", "email_status": "none"})

    db.execute("DELETE FROM kv WHERE key LIKE 'enrolled:%'")
    cfg.data["sending"]["new_leads_per_day"] = 20
    enroll_new_contacts(db, cfg)
    write_due(db, cfg)
    db.execute("UPDATE messages SET status='approved' WHERE status='pending' AND channel='email'")
    send_due(db, cfg, Mailer(cfg), respect_timing=False, max_send=20)
    for cid, text in REPLIES:
        simulate_reply(db, cfg, cid, text)
    # One deal already won to show onboarding
    simulate_reply(db, cfg, 7, "Let's go ahead. Please send the agreement.")
    onboarding.start(db, cfg, 7, value=15000, currency="INR")
    write_due(db, cfg)
    db.close()
    return root
