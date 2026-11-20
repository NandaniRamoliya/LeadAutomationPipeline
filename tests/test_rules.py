"""Compliance checks, classifier, verification and template rendering."""
from leadpipe import compliance
from leadpipe.classifier import rule_classify, strip_quoted
from leadpipe.enrich.verify import is_sendable, verify_email
from leadpipe.inbox import parse_message


# ---- compliance -------------------------------------------------------------
def test_good_intro_passes(cfg):
    body = ("Hi Priya,\n\nI couldn't find a way to book online on smiledental.in. It looks like it's phone only.\n\n"
            "People who look you up late at night usually book wherever it takes one tap.\n\n"
            "Would it be useful if I sent you a free 2-minute video audit of your website?")
    issues = compliance.check_message(cfg, "Quick question about Smile Dental", body, "intro", "Smile Dental Care")
    assert not compliance.blocking(issues), issues


def test_blocks_spammy_first_email(cfg):
    body = ("Dear Sir/Madam,\n\nI hope this email finds you well. We are a leading agency. Our price is ₹9,999. "
            "See https://a.test and https://b.test. Hi [Name]!")
    rules = {i["rule"] for i in compliance.check_message(cfg, "Re: our offer", body, "intro", "Smile")}
    assert {"banned_phrase", "merge_tags", "price_in_first_email", "too_many_links", "misleading_subject"} <= rules


def test_price_allowed_in_reply(cfg):
    issues = compliance.check_message(cfg, "Re: hi", "Thanks. It is around ₹8,000 to ₹15,000.", None, kind="reply")
    assert not compliance.blocking(issues)


def test_footer_has_address_and_optout(cfg):
    f = compliance.footer(cfg, "sequence", "https://x.test/u/1.abc")
    assert "12 MG Road" in f and "no thanks" in f and "Unsubscribe: https://x.test/u/1.abc" in f
    assert "MG Road" not in compliance.footer(cfg, "reply")


# ---- classifier ---------------------------------------------------------------
CASES = {
    "Please remove me from your list.": "unsubscribe",
    "STOP": "unsubscribe",
    "No thanks.": "not_interested",
    "Not interested.": "not_interested",
    "Sounds good, how much would this cost?": "price_question",
    "Yes please send the audit": "interested",
    "Can you share some examples of your previous work?": "portfolio_request",
    "How long does it usually take?": "timeline_question",
    "Not right now, maybe next month.": "not_now",
    "We already have an agency that handles our website.": "already_have",
    "I've booked a slot for Tuesday 11am.": "meeting_booked",
    "Let's go ahead. Send me the agreement.": "ready_to_buy",
    "Please send us a quotation.": "proposal_request",
    "Please contact our manager, ravi@clinic.test, he handles this.": "referral",
}


def test_classifier_cases():
    for text, expected in CASES.items():
        assert rule_classify(text).intent == expected, (text, rule_classify(text).intent)


def test_not_now_delay():
    assert rule_classify("Busy right now, reach out in 3 months").followup_in_days == 90
    assert rule_classify("Not now, next month maybe").followup_in_days == 30


def test_auto_reply_and_bounce_headers():
    assert rule_classify("I am away", "Out of Office: back Monday").intent == "auto_reply"
    assert rule_classify("x", "Hi", {"Auto-Submitted": "auto-replied"}).intent == "auto_reply"
    assert rule_classify("x", "Undeliverable: hello", from_addr="mailer-daemon@googlemail.com").intent == "bounce"


def test_quoted_text_is_ignored():
    text = "Sounds interesting!\n\nOn Mon, 3 Nov 2026 at 10:00, Asha <asha@x.test> wrote:\n> please unsubscribe here"
    assert "unsubscribe" not in strip_quoted(text)
    assert rule_classify(text).intent == "interested"


def test_parse_bounce_message():
    raw = (b"From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>\r\nTo: asha@getpixelstudio.test\r\n"
           b"Subject: Delivery Status Notification (Failure)\r\nMessage-ID: <b1@mail>\r\n"
           b"MIME-Version: 1.0\r\nContent-Type: multipart/report; report-type=delivery-status; boundary=\"XX\"\r\n\r\n"
           b"--XX\r\nContent-Type: text/plain\r\n\r\nAddress not found.\r\n--XX\r\n"
           b"Content-Type: message/delivery-status\r\n\r\nReporting-MTA: dns; google.com\r\n\r\n"
           b"Final-Recipient: rfc822; gone@smiledental.test\r\nAction: failed\r\nStatus: 5.1.1\r\n--XX--\r\n")
    m = parse_message(raw)
    assert m.from_addr == "mailer-daemon@googlemail.com"
    assert "gone@smiledental.test" in m.dsn_recipients
    assert rule_classify(m.body, m.subject, m.headers, m.from_addr).intent == "bounce"


# ---- verification -----------------------------------------------------------------
def test_verify_statuses(cfg):
    assert verify_email("priya@smiledental.test", cfg).status == "valid"
    assert verify_email("info@smiledental.test", cfg).status == "risky"
    assert verify_email("noreply@smiledental.test", cfg).status == "invalid"
    assert verify_email("x@nomail.invalid", cfg).status == "invalid"
    assert verify_email("not-an-email", cfg).status == "invalid"
    assert verify_email("priya@smiledental.test", cfg, guessed=True).status == "guess"
    assert is_sendable("risky", cfg) and not is_sendable("guess", cfg) and not is_sendable("invalid", cfg)
