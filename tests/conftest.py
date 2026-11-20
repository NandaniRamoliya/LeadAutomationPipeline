import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from leadpipe.config import load_config  # noqa: E402
from leadpipe.db import DB  # noqa: E402

TEST_CONFIG = {
    "profile": {
        "your_name": "Asha Patel", "business_name": "Pixel Studio", "website": "https://pixelstudio.test",
        "service": "websites with online booking", "starter_offer": "a booking-ready one-page website in 5 days",
        "price_range": "₹8,000 to ₹15,000", "delivery_time": "5 to 7 working days",
        "proof": ["built booking websites for 12 local clinics"], "proof_links": ["https://pixelstudio.test/work"],
        "quick_win": "a free 2-minute video audit of your website", "booking_link": "https://cal.com/asha/15min",
        "signature": "Asha Patel\nPixel Studio", "physical_address": "12 MG Road, Pune 411001, India",
    },
    "targets": [{"name": "pune-dentists", "area": "Pune, India", "categories": ["dentist"],
                 "timezone": "Asia/Kolkata", "country": "IN", "limit": 10}],
    "llm": {"provider": "none"},
    "engine": {"timezone": "Asia/Kolkata"},
    "sending": {"mode": "dry_run"},
}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "SMTP_HOST", "IMAP_HOST", "DASHBOARD_PASSWORD",
              "HUNTER_API_KEY", "NOTIFY_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FROM_EMAIL", "asha@getpixelstudio.test")
    monkeypatch.setenv("LEADPIPE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LEADPIPE_DB", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(TEST_CONFIG, allow_unicode=True), encoding="utf-8")
    # Every domain "has" MX records in tests (no real DNS).
    from leadpipe.enrich import verify
    monkeypatch.setattr(verify, "mx_hosts", lambda domain: ("mx." + domain,) if not domain.endswith(".invalid") else ())
    return load_config(path)


@pytest.fixture
def db(cfg):
    d = DB(cfg.db_path)
    yield d
    d.close()
