"""Built-in copy: buying signals, fallback email templates (used when no AI is configured, or when an
AI draft fails the safety checks), the "Closer" replies, and onboarding emails.

Every template follows the playbooks: their name, their real problem, what it costs them, one quick
win, one soft yes/no question. No "Dear Sir/Madam", no fake compliments, no price in a first message.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Buying signals: what the Auditor can detect on a website without any AI cost.
# ---------------------------------------------------------------------------
SIGNALS: dict[str, dict[str, str]] = {
    "no_website": {
        "label": "No website",
        "explain": "Only a map/listing, no website of their own.",
        "problem": "No website, only a map listing",
        "observation": "I looked {company} up and could only find the map listing, not a website.",
        "cost": "People comparing options on their phone usually pick the place they can check out properly.",
        "fix": "a simple one-page site with timings, photos and a contact button",
    },
    "site_broken": {
        "label": "Website broken",
        "explain": "Website returns an error or doesn't load.",
        "problem": "Website doesn't load / shows an error",
        "observation": "I tried opening {website} and it didn't load properly for me.",
        "cost": "Anyone clicking through from Google right now hits a dead end.",
        "fix": "getting the site back up and stable",
    },
    "instagram_only": {
        "label": "Instagram / Facebook only",
        "explain": "Their 'website' is a social media page.",
        "problem": "Uses Instagram/Facebook instead of a website",
        "observation": "The website link for {company} points to a social media page rather than a site.",
        "cost": "That's great for showing your work, but people searching Google can't easily see timings, prices or how to book.",
        "fix": "a simple site that Google can show, linked to your Instagram",
    },
    "no_online_booking": {
        "label": "No online booking",
        "explain": "No booking/appointment widget or 'Book' button.",
        "problem": "No way to book online; call/WhatsApp only",
        "observation": "I couldn't find a way to book online on {website}. It looks like it's phone or WhatsApp only.",
        "cost": "People who look you up late at night usually book wherever it takes one tap.",
        "fix": "a 'Book now' button connected to your calendar",
    },
    "not_mobile_friendly": {
        "label": "Not mobile-friendly",
        "explain": "No mobile viewport: the site shows tiny on phones.",
        "problem": "Site isn't set up for phones (needs zooming)",
        "observation": "I checked {website} and it doesn't seem to be set up for phones, so on mobile everything shows up tiny.",
        "cost": "Most local searches happen on phones, so many visitors leave before reading anything.",
        "fix": "a mobile-first layout",
    },
    "slow_site": {
        "label": "Slow website",
        "explain": "Homepage took more than 3 seconds to respond.",
        "problem": "Slow to load",
        "observation": "When I opened {website}, it took a few seconds to load.",
        "cost": "On mobile, every extra second loses visitors before they see anything.",
        "fix": "a lighter, faster homepage",
    },
    "no_https": {
        "label": "No HTTPS",
        "explain": "No SSL certificate: browsers show 'Not secure'.",
        "problem": "Browser shows 'Not secure' (no SSL)",
        "observation": "Chrome shows {website} as 'Not secure' because it has no SSL certificate.",
        "cost": "That warning makes people hesitate before calling or filling in a form.",
        "fix": "a free SSL certificate and a quick security clean-up",
    },
    "outdated_site": {
        "label": "Outdated website",
        "explain": "Copyright year is old: the site hasn't been touched in years.",
        "problem": "Site looks outdated (old copyright year)",
        "observation": "The footer on {website} still says {copyright_year}.",
        "cost": "Small details like that make visitors wonder whether the information is still current.",
        "fix": "a refreshed homepage with up-to-date details",
    },
    "running_ads": {
        "label": "Running ads",
        "explain": "Google Ads / Meta Pixel found: they spend money on traffic.",
        "problem": "Paying for ads, but the landing page doesn't convert well",
        "observation": "It looks like {company} is running ads (I spotted the ad tracking on the site).",
        "cost": "If the page people land on doesn't make the next step obvious, part of that ad budget leaks away.",
        "fix": "a landing page built around one clear action",
    },
    "hiring": {
        "label": "Hiring",
        "explain": "Careers/jobs page or 'we're hiring' text: a growing business.",
        "problem": "Growing team: admin and follow-ups get stretched",
        "observation": "I saw that {company} is hiring at the moment.",
        "cost": "Growing teams usually feel it first in the admin: enquiries, bookings and follow-ups.",
        "fix": "automating the repetitive enquiry and booking steps",
    },
    "no_contact_form": {
        "label": "No contact form",
        "explain": "No form on the site, only a phone number/email.",
        "problem": "No contact form; phone only",
        "observation": "There's no contact form on {website}, only a phone number.",
        "cost": "People who can't call during work hours have no easy way to reach you.",
        "fix": "a short enquiry form that lands straight in your WhatsApp or inbox",
    },
    "weak_seo": {
        "label": "Weak SEO basics",
        "explain": "Missing page title or meta description.",
        "problem": "Missing Google title/description",
        "observation": "I noticed {website} has no page description set, so Google shows a fairly random snippet for it.",
        "cost": "That snippet is the first impression for everyone who searches for you.",
        "fix": "proper titles and descriptions for the main pages",
    },
    "no_analytics": {
        "label": "No analytics",
        "explain": "No Google Analytics / Tag Manager found.",
        "problem": "No visitor tracking",
        "observation": "I couldn't find any analytics on {website}.",
        "cost": "So there's no way to see how many people visit and how many actually get in touch.",
        "fix": "simple tracking so you can see which pages bring enquiries",
    },
    "diy_builder": {
        "label": "DIY site builder",
        "explain": "Built on Wix / GoDaddy / Weebly / Google Sites: usually a self-made site.",
        "problem": "Self-made site on a DIY builder",
        "observation": "It looks like {website} was built on {builder}.",
        "cost": "Those builders are a great start, but they usually load slowly and are hard to rank on Google.",
        "fix": "a faster, professionally built site you fully own",
    },
    "agency_built": {
        "label": "Built by an agency",
        "explain": "Footer credits a web/marketing agency ('Website by ...').",
        "problem": "Site built by an agency (may not be actively looked after)",
        "observation": "I noticed {website} was built by {agency}.",
        "cost": "When an agency has moved on to other clients, small issues like the ones I spotted tend to pile up.",
        "fix": "a quick refresh and someone who actually looks after it",
    },
    "no_schema_markup": {
        "label": "No schema markup",
        "explain": "No structured data: Google can't show rich results (stars, hours, prices).",
        "problem": "No structured data for Google",
        "observation": "{website} has no structured data, so Google can't show your hours, ratings or prices in search.",
        "cost": "Competitors with star ratings in search results get the click first.",
        "fix": "adding schema markup so Google shows rich results",
    },
    "blog_stale": {
        "label": "Blog abandoned",
        "explain": "The blog's newest post is more than a year old.",
        "problem": "Blog not updated since {last_post_year}",
        "observation": "The newest post on the {company} blog seems to be from {last_post_year}.",
        "cost": "An abandoned blog quietly tells visitors (and Google) the site isn't looked after.",
        "fix": "a simple content plan, or removing the old blog",
    },
    "low_rating": {
        "label": "Low Google rating",
        "explain": "Average rating under 4.0 stars.",
        "problem": "Google rating {rating} stars",
        "observation": "{company} has a {rating}-star average on Google.",
        "cost": "Most people filter for 4 stars and up, so many never even see the listing.",
        "fix": "a simple system that asks happy customers for reviews",
    },
    "few_reviews": {
        "label": "Few reviews",
        "explain": "Fewer than 15 Google reviews.",
        "problem": "Only {reviews} Google reviews",
        "observation": "{company} only has {reviews} reviews on Google.",
        "cost": "Businesses nearby with more reviews usually get picked first.",
        "fix": "an automatic review request after every visit",
    },
    "startup_backed": {
        "label": "Startup (accelerator)",
        "explain": "Their own site says they're backed by YC / Techstars / similar.",
        "problem": "Early-stage startup ({backer})",
        "observation": "I saw that {company} is backed by {backer}.",
        "cost": "At this stage, every week spent on things outside the core product is expensive.",
        "fix": "taking this off your plate so the team can focus on the product",
    },
    "recently_funded": {
        "label": "Recently raised",
        "explain": "Their own site announces a funding round.",
        "problem": "Just raised a {round}",
        "observation": "Congratulations on the {round}!",
        "cost": "Right after a raise, the pressure is on to grow fast without breaking what already works.",
        "fix": "getting this in place quickly so it's ready for the growth",
    },
    "no_live_chat": {
        "label": "No chat / WhatsApp button",
        "explain": "No chat widget or WhatsApp click-to-chat.",
        "problem": "No chat or WhatsApp button",
        "observation": "There's no chat or WhatsApp button on {website}.",
        "cost": "Quick questions like 'are you open on Sunday?' turn into lost visitors instead of bookings.",
        "fix": "a WhatsApp chat button with quick auto-replies",
    },
}


# Short noun phrase per signal, for sentences like "If online booking ever becomes a priority".
TOPICS = {"no_website": "a website", "site_broken": "your website", "instagram_only": "a proper website",
          "no_online_booking": "online booking", "not_mobile_friendly": "a mobile-friendly site", "slow_site": "site speed",
          "no_https": "website security", "outdated_site": "a website refresh", "running_ads": "your ad landing page",
          "hiring": "automating admin work", "no_contact_form": "an enquiry form", "weak_seo": "Google visibility",
          "no_analytics": "website tracking", "no_live_chat": "WhatsApp chat on your site", "diy_builder": "a faster website",
          "agency_built": "your website", "no_schema_markup": "Google rich results", "blog_stale": "your blog",
          "low_rating": "your Google reviews", "few_reviews": "more Google reviews", "startup_backed": "this",
          "recently_funded": "this"}
for _k, _t in TOPICS.items():
    if _k in SIGNALS:
        SIGNALS[_k]["topic"] = _t


class SafeDict(dict):
    """Missing keys render as '' instead of crashing, so no placeholder ever leaks into an email."""

    def __missing__(self, key: str) -> str:  # pragma: no cover - trivial
        return ""


def fill(template: str, ctx: dict) -> str:
    out = template.format_map(SafeDict(ctx))
    # Tidy up whitespace left by empty optional fields.
    lines = [ln.rstrip() for ln in out.splitlines()]
    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.replace("  ", " ").strip()


def pick(options: list, seed: str):
    idx = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(options)
    return options[idx]


@dataclass
class Draft:
    subject: str
    body: str
    generator: str = "template"
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sequence emails. Each purpose has several variants so emails are never identical.
# Subject None = reply in the same thread ("Re: <first subject>").
# ---------------------------------------------------------------------------
SEQUENCE_TEMPLATES: dict[str, list[tuple[str | None, str]]] = {
    "intro": [
        ("Quick question about {company}",
         "{greeting}\n\n{obs1} {cost1}\n\n{proof_line}I help businesses like yours fix exactly this, usually with {fix1}.\n\n"
         "Would it be useful if I sent you {quick_win}?"),
        ("Something I noticed about {company}",
         "{greeting}\n\n{obs1}\n\n{cost1}\n\n{proof_line}Would you like me to send over {quick_win}? It's yours to keep either way."),
        ("An idea for {company}",
         "{greeting}\n\n{obs1}\n\n{cost1} The fix is usually small: {fix1}.\n\n{proof_line}"
         "Open to me sending {quick_win}? No call needed."),
    ],
    "bump": [
        (None, "{greeting}\n\nOne more thing I noticed: {obs2_or_fix}\n\nBoth are quick fixes. Should I send the {quick_win_short}?"),
        (None, "{greeting}\n\nSending this back to the top of your inbox. The {quick_win_short} for {company} takes me a few minutes to make. Want it?"),
    ],
    "insight": [
        (None, "{greeting}\n\nIf {company}'s site were mine, the first thing I'd change is {fix1}. "
               "It's the simplest way to stop losing the people who already found you.\n\n{obs2_line}"
               "Happy to show you what that would look like for {company}. Worth a look?"),
        (None, "{greeting}\n\nA thought on {company}: {cost2_or_cost1}\n\nThe businesses I work with usually start with {fix1}, "
               "and only add more once that works.\n\nShall I sketch how it would look for you?"),
    ],
    "value": [
        (None, "{greeting}\n\nTo make this easy: reply \"yes\" and I'll make {quick_win} for {company}: "
               "three specific fixes, in plain words, yours to keep whether we work together or not.\n\n{proof_link_line}Interested?"),
        (None, "{greeting}\n\nI'd rather show than tell. If you're open to it, I'll put together {quick_win} for {company} "
               "with the 3 changes I'd make first. No strings attached.\n\n{proof_link_line}Should I go ahead?"),
    ],
    "breakup": [
        (None, "{greeting}\n\nI haven't heard back, so I'll assume the timing isn't right and won't keep emailing.\n\n"
               "If {topic1} ever moves up the list, just reply here and I'll pick it up. All the best to the team at {company}."),
        (None, "{greeting}\n\nLast note from me, I don't want to clutter your inbox. If {topic1} becomes a priority "
               "later this year, reply anytime. Wishing {company} a great season."),
    ],
    # ---- warm: interested, no call booked yet --------------------------------
    "booking_nudge": [
        (None, "{greeting}\n\nTo make it easy, here's my calendar: {booking_link}\n\n15 minutes is enough for me to show you "
               "exactly what I'd do for {company}, and you decide after. Does this week work?"),
        (None, "{greeting}\n\nWould a quick 15-minute call this week work? Pick any slot here: {booking_link}. "
               "I'll come with specific ideas for {company}, not a sales pitch."),
    ],
    "proof": [
        (None, "{greeting}\n\nIn case it helps while you decide: I {proof1}. {proof_link_line}\n\n"
               "Happy to walk you through how the same would work for {company}. Shall I hold a slot for you? {booking_link}"),
    ],
    "close_loop": [
        (None, "{greeting}\n\nI don't want to crowd your inbox, so I'll close this out for now. If you'd still like "
               "{starter_offer}, reply with a time that suits you and I'll make it happen."),
    ],
    # ---- proposal: call done, waiting for a decision -------------------------
    "proposal_followup": [
        (None, "{greeting}\n\nThanks again for your time. Is there anything in the proposal you'd like me to change: "
               "scope, timeline or anything else? Happy to adjust so it fits {company}."),
    ],
    "objection_preempt": [
        (None, "{greeting}\n\nA few questions people usually ask at this stage, in case they help:\n\n"
               "- How long does it take? {delivery_time}.\n- What do you need from us? Just a few accesses and one kickoff call.\n"
               "- What if we want changes? Revisions are included and written into the agreement.\n\nAnything else I can answer?"),
    ],
    "decision_nudge": [
        (None, "{greeting}\n\nIf committing to the whole project feels like a lot right now, we could start with just "
               "{starter_offer}, so you see results first. Would that be easier?"),
    ],
    "close_file": [
        (None, "{greeting}\n\nI'll close your file on my side for now so I'm not chasing you. Everything we discussed still stands. "
               "If you want to pick it up later, just reply to this email."),
    ],
    # ---- nurture: not now --------------------------------------------------------
    "nurture_value": [
        ("Checking back in, {company}",
         "{greeting}\n\nIt's been a while since we spoke. You mentioned the timing wasn't right, so I wanted to check back as promised.\n\n"
         "The thing I'd still start with for {company} is {fix1}. Is now a better time to look at it?"),
    ],
    "nurture_idea": [
        ("A small idea for {company}",
         "{greeting}\n\nHad a quick idea for {company}: {fix2_or_fix1}. It's a small change, but it's the one I see make the biggest "
         "difference for {category_plural}.\n\nWant me to send a quick mock-up?"),
    ],
    "nurture_final": [
        (None, "{greeting}\n\nLast check-in from me. If {service} becomes a priority this year, just reply and I'll be glad to help. "
               "Otherwise, all the best to you and the team at {company}."),
    ],
    # ---- client care: after onboarding --------------------------------------
    "care_checkin": [
        ("How is everything going?",
         "{greeting}\n\nIt's been a couple of weeks since we wrapped up. How is everything working on your side? "
         "If anything needs a tweak, just reply and I'll take care of it."),
    ],
    "care_feedback": [
        ("A small favour",
         "{greeting}\n\nCould you share 2-3 lines about working together? A short testimonial helps me a lot. "
         "And if anything could have gone better, I'd really like to hear that too."),
    ],
    "care_referral": [
        ("Do you know anyone?",
         "{greeting}\n\nIf you know another business owner dealing with the same problem we fixed for {company}, "
         "I'd be grateful for an introduction. I'll take good care of them, and I'll keep you posted."),
    ],
    "care_next": [
        ("Next step for {company}",
         "{greeting}\n\nI've been thinking about what could help {company} next: {fix2_or_next}. "
         "Want me to put together a short plan with timeline and cost?"),
    ],
    "referral_intro": [
        ("{referrer} suggested I reach out",
         "{greeting}\n\n{referrer} suggested I get in touch with you. I help businesses like {company} with {service}, "
         "and I noticed {obs1_lc}\n\nWould it be useful if I sent you {quick_win}?"),
    ],
    # ---- DMs (WhatsApp / Instagram / LinkedIn / X / Facebook): short, no subject, no links ----
    "dm_intro": [
        (None, "Hi {first_or_team}! {obs1} {cost1_short}\n\nWould you like {quick_win}? No strings attached."),
        (None, "Hi {first_or_team}, quick one: {obs1_lc} {cost1_short} Happy to send you {quick_win} if useful?"),
    ],
    "dm_touch": [
        (None, "Hi {first_or_team}, I emailed you an idea about {topic1} for {company}. Messaging here in case it's easier. "
               "Want me to send {quick_win}?"),
    ],
    "dm_followup": [
        (None, "Hi {first_or_team}, one more idea for {company}: {fix2_or_fix1}. Should I send a quick mock-up?"),
    ],
    "dm_final": [
        (None, "Last message from me, {first_or_team}. If {topic1} ever becomes a priority, just reply here. All the best!"),
    ],
}

# Call scripts (phone tasks). Plain structure you can read from while dialling.
CALL_TEMPLATES: dict[str, str] = {
    "call_intro": (
        "CALL {company}{phone_line}\n\n"
        "1) OPENER: \"Hi, is this {first_or_owner}? This is {your_name} from {business_name}. I'll be quick, "
        "I noticed something on your {online_presence} and have a small idea. Do you have 30 seconds?\"\n\n"
        "2) WHY YOU'RE CALLING: {obs1}\n   What it costs them: {cost1}\n\n"
        "3) ASK (listen more than you talk):\n   - How do most new customers find or book you today?\n"
        "   - What happens when someone contacts you after hours?\n\n"
        "4) OFFER: \"I can send you {quick_win}, free. Best on WhatsApp or email?\"\n\n"
        "5) IF INTERESTED: book 15 minutes → {booking_link}\n\n"
        "VOICEMAIL (20 sec): \"Hi, {your_name} from {business_name}. I noticed something about {topic1} and have a quick idea "
        "for {company}. I'll send a short message, reply whenever it suits you. Thanks!\"\n\n"
        "IF THEY SAY...\n   \"Not interested\" → \"No problem at all, thanks for your time.\" (log: Not interested)\n"
        "   \"Send me details\" → \"Sure, what's the best WhatsApp number or email?\" (log: Interested)\n"
        "   \"How much?\" → \"Usually {price_range}, depends on what you need. That's why 15 minutes helps.\"\n"
        "   \"We have someone\" → \"Great, happy to give a free second opinion anytime.\""),
    "call_followup": (
        "CALL {company}{phone_line} (follow-up)\n\n"
        "1) OPENER: \"Hi {first_or_owner}, {your_name} from {business_name}. I sent you a note about {topic1} for {company}. "
        "Is now an OK time for 2 minutes?\"\n\n"
        "2) GOAL: book the 15-minute call → {booking_link}\n   Offer two times: \"Would tomorrow 11am or Thursday 4pm work?\"\n\n"
        "3) If they hesitate: \"What would you need to see to feel sure?\"\n\n"
        "VOICEMAIL: \"Hi {first_or_owner}, {your_name} here. Just following up on {company}. I'll send the booking link by "
        "message. Thanks!\""),
}

# ---------------------------------------------------------------------------
# The Closer: replies to the most common answers. Every reply moves toward a 15-minute call.
# ---------------------------------------------------------------------------
REPLY_TEMPLATES: dict[str, str] = {
    "interested": (
        "{greeting_thanks}\n\nThe easiest next step is a quick 15-minute call where I show you exactly what I'd do for {company}. "
        "You can pick a time here: {booking_link}\n\nIf none of the slots work, just send me two times that suit you."),
    "price_question": (
        "{greeting_thanks}\n\nGood question. Most projects like this land around {price_range}, depending on what's included. "
        "{starter_offer_cap} is the usual starting point.\n\nThe exact number depends on a couple of details. Could we do a quick "
        "15-minute call so I can give you a fixed quote? {booking_link}"),
    "portfolio_request": (
        "{greeting_thanks}\n\nOf course. {portfolio_line}\n\nHappy to show you how the same would look for {company} on a quick "
        "15-minute call: {booking_link}"),
    "timeline_question": (
        "{greeting_thanks}\n\nUsually {delivery_time} from the day I have everything I need. If you tell me a bit more on a "
        "quick 15-minute call, I'll confirm a firm date: {booking_link}"),
    "not_now": (
        "{greeting_thanks}\n\nTotally understood. Would it be okay if I check back in a couple of months? "
        "If anything changes before then, just reply here."),
    "already_have": (
        "{greeting_thanks}\n\nThat's good to hear. If you ever want a second opinion, I'm happy to do {quick_win}, "
        "no strings attached. Wishing you and {company} all the best."),
    "meeting_booked": (
        "{greeting_thanks}\n\nPerfect, it's in my calendar. I'll come prepared with a few specific ideas for {company}. Talk soon."),
    "ready_to_buy": (
        "{greeting_thanks}\n\nFantastic, I'm looking forward to it. I'll send over a short agreement with scope, timeline and "
        "payment terms shortly. Once it's signed, we'll lock in the kickoff."),
    "not_interested": "{greeting_thanks}\n\nUnderstood, and thanks for the quick reply. I won't follow up. All the best with {company}.",
    "proposal_request": (
        "{greeting_thanks}\n\nAbsolutely. I'll put together a short proposal for {company} with scope, timeline and price. "
        "To make it accurate rather than generic, could we do a quick 15-minute call first? {booking_link}\n\n"
        "If you'd rather skip the call, just reply with what matters most to you and I'll work from that."),
    "referral": "{greeting_thanks}\n\nReally appreciate it. I'll reach out to them and mention that you pointed me their way.",
    "question": (
        "{greeting_thanks}\n\nGood question, and I want to give you a proper answer rather than a generic one. Could we do a quick "
        "15-minute call? {booking_link}"),
}


# ---------------------------------------------------------------------------
# Client onboarding emails (Contract > Payment > Welcome > Kickoff > Access > Portal)
# ---------------------------------------------------------------------------
ONBOARDING_TEMPLATES: dict[str, tuple[str, str]] = {
    "contract": (
        "Next steps for {company}: agreement",
        "{greeting}\n\nGreat to be working together! Here's how we'll get started:\n\n"
        "1. Agreement: {contract_line}\n2. Payment: once it's signed, I'll send the invoice.\n"
        "3. Welcome document and a kickoff call, then work begins.\n\n"
        "The agreement covers scope, deliverables, timeline, revisions and payment terms, so we're both clear before anything starts. "
        "Any questions, just reply here."),
    "contract_reminder": (
        "Re: Next steps for {company}: agreement",
        "{greeting}\n\nA quick reminder about the agreement ({contract_line}). As soon as it's signed I'll send the invoice "
        "and we can book the kickoff. Anything in it you'd like me to change?"),
    "invoice": (
        "Invoice for {company}",
        "{greeting}\n\nThanks for signing! Here's the payment link: {payment_line}\n\n"
        "Once the payment is in, I'll send the welcome document and we'll book the kickoff call."),
    "payment_reminder": (
        "Re: Invoice for {company}",
        "{greeting}\n\nA gentle reminder about the invoice: {payment_line}. As soon as it's settled I'll send the welcome "
        "document and we can get started."),
    "welcome": (
        "Welcome aboard, {company}!",
        "{greeting}\n\nPayment received, thank you, and welcome aboard! {welcome_doc_line}\n\n"
        "- What happens next: a kickoff call, then I start work.\n- What I need from you: a few accesses (I'll send the list after the call).\n"
        "- Timeline: {delivery_time}.\n- Where we talk: this email thread, so nothing gets lost.\n\n"
        "Please book the kickoff call here: {kickoff_link}"),
    "kickoff_reminder": (
        "Re: Welcome aboard, {company}!",
        "{greeting}\n\nWhenever you're ready, please grab a kickoff slot here: {kickoff_link}. It takes about 30 minutes "
        "and it's where we agree on goals, audience and next steps."),
    "access_request": (
        "Access needed to start {company}'s project",
        "{greeting}\n\nThanks for the kickoff call! To start without delays, I need:\n\n{access_list}\n\n"
        "Please send access as user invites where possible. Never share passwords over chat or email."),
    "access_reminder": (
        "Re: Access needed to start {company}'s project",
        "{greeting}\n\nA quick reminder about the access list below. It's the last thing before I can start:\n\n{access_list}"),
    "portal_live": (
        "Everything for {company}'s project in one place",
        "{greeting}\n\nEverything for the project now lives in one place: {portal_link}\n\n"
        "There you'll find the timeline, tasks, deliverables, files, reports and meeting notes. Bookmark it, "
        "and you'll never need to search your inbox for anything."),
}


def plural(category: str) -> str:
    c = (category or "local business").replace("_", " ").strip()
    if c.endswith("y") and not c.endswith(("ay", "ey", "oy")):
        return c[:-1] + "ies"
    if c.endswith(("s", "sh", "ch")):
        return c + "es"
    return c + "s"
