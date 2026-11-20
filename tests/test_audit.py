"""Website audit against a real local HTTP server serving fixture pages."""
import functools
import http.server
import threading
from pathlib import Path

import pytest

from leadpipe.enrich import enrich_company
from leadpipe.enrich.website import audit_website, decode_cfemail

SITE = Path(__file__).parent / "fixtures" / "site"


@pytest.fixture(scope="module")
def site_url():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    srv.shutdown()


def test_audit_finds_signals_people_and_emails(site_url):
    a = audit_website(site_url, timeout=5, this_year=2026)
    assert a.ok and a.pages_read == 3
    for sig in ("no_https", "not_mobile_friendly", "outdated_site", "no_online_booking", "no_contact_form",
                "no_analytics", "weak_seo"):
        assert sig in a.signals, (sig, a.signals)
    assert a.signals["outdated_site"]["copyright_year"] == "© 2017"
    assert "priya@smiledental.test" in a.emails and "info@smiledental.test" in a.emails
    assert not any(e.endswith(".png") for e in a.emails)
    names = [p["full_name"] for p in a.people]
    assert any("Priya Sharma" in n for n in names), a.people


def test_no_website_and_social_only():
    assert "no_website" in audit_website(None).signals
    s = audit_website("https://instagram.com/smiledental").signals
    assert "instagram_only" in s


def test_cloudflare_email_decode():
    key = 0x54
    encoded = f"{key:02x}" + "".join(f"{ord(ch) ^ key:02x}" for ch in "me@xo.com")
    assert decode_cfemail(encoded) == "me@xo.com"


def test_enrich_creates_ranked_contacts_and_qualifies(cfg, db, site_url):
    cid = db.insert("companies", {"name": "Smile Dental Care", "website": site_url, "domain": "smiledental.test",
                                  "category": "dentist", "city": "Pune", "timezone": "Asia/Kolkata", "status": "new"})
    co = enrich_company(db, cfg, db.company(cid))
    assert co["status"] == "qualified", co["status_reason"]
    cts = db.all("SELECT * FROM contacts WHERE company_id = ? ORDER BY id", (cid,))
    assert [c["email"] for c in cts] == ["priya@smiledental.test", "info@smiledental.test"]
    assert cts[0]["first_name"].startswith("Dr") and cts[0]["email_status"] == "valid"
    assert co["score"] >= 25
