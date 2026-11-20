import re

from leadpipe.dashboard.app import create_app
from leadpipe.pipeline import enroll_new_contacts, is_suppressed
from leadpipe.sender import unsub_token
from leadpipe.writer import write_due

from .test_end_to_end import approve_all, send_all, setup_leads


def _client(cfg, db):
    app = create_app(cfg, db)
    app.testing = True
    return app.test_client()


def _csrf(client):
    html = client.get("/").get_data(as_text=True)
    return re.search(r'name="_csrf" value="([0-9a-f]+)"', html).group(1)


def test_pages_render(cfg, db):
    setup_leads(db, cfg)
    enroll_new_contacts(db, cfg)
    write_due(db, cfg)
    c = _client(cfg, db)
    for path in ["/", "/outbox", "/replies", "/manual", "/leads", "/contacts", "/clients", "/analytics", "/settings",
                 "/contact/1", "/company/1", "/health"]:
        r = c.get(path, follow_redirects=True)
        assert r.status_code == 200, (path, r.status_code)
    assert "Smile Dental Care" in c.get("/outbox").get_data(as_text=True)


def test_approve_requires_csrf_and_works(cfg, db):
    setup_leads(db, cfg)
    enroll_new_contacts(db, cfg)
    write_due(db, cfg)
    c = _client(cfg, db)
    m = db.one("SELECT * FROM messages WHERE status='pending' LIMIT 1")
    assert c.post(f"/outbox/{m['id']}/approve", data={"subject": m["subject"], "body": m["body"]}).status_code == 400
    token = _csrf(c)
    r = c.post(f"/outbox/{m['id']}/approve", data={"_csrf": token, "subject": m["subject"], "body": m["body"]})
    assert r.status_code == 302 and db.message(m["id"])["status"] == "approved"
    # Editing in a banned phrase blocks approval
    m2 = db.one("SELECT * FROM messages WHERE status='pending' LIMIT 1")
    c.post(f"/outbox/{m2['id']}/approve", data={"_csrf": token, "subject": m2["subject"],
                                                 "body": "Dear Sir/Madam, I hope this email finds you well."})
    assert db.message(m2["id"])["status"] == "pending"


def test_password_protection(cfg, db, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")
    c = _client(cfg, db)
    assert c.get("/").status_code == 401
    import base64
    auth = {"Authorization": "Basic " + base64.b64encode(b"me:s3cret").decode()}
    assert c.get("/", headers=auth).status_code == 200
    assert c.get("/health").status_code == 200  # public


def test_unsubscribe_link_and_booking_webhook(cfg, db, monkeypatch):
    setup_leads(db, cfg)
    enroll_new_contacts(db, cfg)
    write_due(db, cfg)
    approve_all(db)
    send_all(db, cfg)
    c = _client(cfg, db)
    amit = db.one("SELECT * FROM contacts WHERE email='amit@brightclinic.test'")
    tok = unsub_token(db, cfg, amit["id"])
    assert "Unsubscribe?" in c.get(f"/u/{tok}").get_data(as_text=True)
    assert c.get(f"/u/{amit['id']}.forged").status_code == 200  # shows "not valid", changes nothing
    assert db.contact(amit["id"])["stage"] == "cold"
    c.post(f"/u/{tok}")  # one-click, no CSRF needed
    assert db.contact(amit["id"])["stage"] == "unsubscribed" and is_suppressed(db, amit["email"])

    monkeypatch.setenv("BOOKING_WEBHOOK_TOKEN", "hook123")
    priya = db.one("SELECT * FROM contacts WHERE email='priya@smiledental.test'")
    assert c.post("/webhooks/booking?token=wrong", json={}).status_code == 403
    payload = {"triggerEvent": "BOOKING_CREATED", "payload": {"startTime": "2026-11-25T06:00:00Z",
               "attendees": [{"email": "priya@smiledental.test", "name": "Priya"}]}}
    r = c.post("/webhooks/booking?token=hook123", json=payload)
    assert r.get_json()["matched_contacts"] == [priya["id"]]
    assert db.contact(priya["id"])["stage"] == "meeting"
