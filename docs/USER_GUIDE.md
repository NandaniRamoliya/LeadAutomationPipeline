# User Guide: LeadAutomationPipeline

This guide explains **what the system does, how it works, how to set it up, and how to use it every day**.
No programming knowledge is needed beyond copying commands.

**Contents**

1. [What it does, in plain words](#1-what-it-does-in-plain-words)
2. [What you need before you start](#2-what-you-need-before-you-start)
3. [Install](#3-install)
4. [Try the demo (5 minutes)](#4-try-the-demo-5-minutes)
5. [Set it up for your business](#5-set-it-up-for-your-business)
6. [Your first week](#6-your-first-week)
7. [Your daily routine (15 minutes)](#7-your-daily-routine-15-minutes)
8. [The dashboard, page by page](#8-the-dashboard-page-by-page)
9. [How leads are found and qualified](#9-how-leads-are-found-and-qualified)
10. [How emails are written and checked](#10-how-emails-are-written-and-checked)
11. [The follow-up logic (every path)](#11-the-follow-up-logic-every-path)
12. [When a lead says YES: onboarding](#12-when-a-lead-says-yes-onboarding)
13. [Going live: real emails](#13-going-live-real-emails)
14. [Running 24/7](#14-running-247)
15. [Unsubscribe links and calendar bookings (public URL)](#15-unsubscribe-links-and-calendar-bookings-public-url)
16. [Alerts on your phone](#16-alerts-on-your-phone)
17. [Command reference](#17-command-reference)
18. [Troubleshooting](#18-troubleshooting)
19. [FAQ](#19-faq)

---

## 1. What it does, in plain words

Think of it as a small sales team that never sleeps:

| "Employee" | Job | Runs |
|---|---|---|
| **Lead Hunter** | Finds businesses in your city and niche (OpenStreetMap is free, or Google Places, or your CSV) | every few hours, when the queue is low |
| **Auditor** | Opens each website and notes real, checkable problems: no online booking, not mobile-friendly, slow, "Not secure", old copyright year, no website at all... Finds the owner's name and email | every 20 min |
| **Verifier** | Checks every email address can receive mail. Bad ones are never used | with the Auditor |
| **Writer** | Writes one short, personal email per person and per follow-up, based on *their* problem | every 10 min |
| **Sender** | Sends approved emails one at a time, inside the recipient's office hours, under your daily limit | every minute |
| **Inbox reader** | Reads replies, understands them (interested? price question? not now? unsubscribe?) | every 5 min |
| **Closer** | Drafts the right answer and moves the lead to the right follow-up track | on every reply |
| **Onboarder** | After "yes": contract, invoice, welcome document, kickoff, access list, portal, with reminders | every minute |
| **Analyst** | Shows which emails, problems and business types actually get replies | always |

**What you do:** approve emails (or switch on autopilot later), take the calls, send proposals, do the work,
and tick onboarding steps only you can confirm ("contract signed", "payment received").

**The golden rule** (from every playbook): *one real, personal message beats 100 templates*. The system is
built to send fewer, better emails, not more.

---

## 2. What you need before you start

| Item | Why | Cost |
|---|---|---|
| **Python 3.10+** | Runs the program | free |
| **A separate sending domain** (e.g. `getpixelstudio.com` if your site is `pixelstudio.com`) | Protects your main domain's reputation if anything goes wrong | ~₹800–1,000 / $10 a year |
| **A mailbox on that domain** (Google Workspace best, Microsoft 365 / Zoho fine) | Sends and receives the emails | ~$7/month |
| **SPF, DKIM, DMARC** DNS records on the sending domain | Without them Gmail puts you in spam | free |
| **2–4 weeks of mailbox warm-up** | A brand-new mailbox that suddenly sends cold email = spam | free (normal use) or a warm-up service |
| **A calendar booking link** (Cal.com free, Calendly, Google Calendar booking page) | Every reply pushes toward a 15-minute call | free |
| **A real postal address** | Legally required in commercial email in the US (CAN-SPAM) and good practice everywhere | — |
| *Optional:* Claude API key or Claude Pro/Max plan | AI-written emails and smarter reply reading | pay as you go / your plan |

### Setting up the sending domain (Google Workspace example)

1. Buy the domain (Namecheap, GoDaddy, Cloudflare...).
2. Create Google Workspace on it and make one mailbox, e.g. `asha@getpixelstudio.com`.
3. Add these DNS records at your domain provider:
   - **SPF** (TXT on `@`): `v=spf1 include:_spf.google.com ~all`
   - **DKIM**: Google Admin → Apps → Gmail → Authenticate email → *Generate new record* → add the TXT it shows (name `google._domainkey`) → click *Start authentication*.
   - **DMARC** (TXT on `_dmarc`): `v=DMARC1; p=none; rua=mailto:you@yourmaindomain.com`
4. Make the "from" name a real person, add a profile photo, and **forward** the sending domain's website to your main site.
5. Create an **App Password** (Google Account → Security → 2-Step Verification → App passwords). You'll put it in `.env`.
6. **Warm up**: for 2–4 weeks, use the mailbox normally (send real emails to people you know, sign up to newsletters, reply).
   The pipeline also ramps up slowly by itself (5 emails on day 1, +2 per day).

`leadpipe doctor` checks SPF, DKIM and DMARC for you.

---

## 3. Install

**Mac / Linux**

```bash
git clone https://github.com/NandaniRamoliya/LeadAutomationPipeline.git
cd LeadAutomationPipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

(On a Mac, if `python3 --version` says 3.9, install a newer one: `brew install python@3.13` and use `python3.13 -m venv .venv`.)

**Windows** (PowerShell)

```powershell
git clone https://github.com/NandaniRamoliya/LeadAutomationPipeline.git
cd LeadAutomationPipeline
py -3 -m venv .venv
.venv\Scripts\activate
pip install -e .
```

Check it works: `leadpipe --help`.

> Every time you open a new terminal, first `cd LeadAutomationPipeline` and activate the venv
> (`source .venv/bin/activate` or `.venv\Scripts\activate`). If you see `command not found: leadpipe`, you forgot this.

---

## 4. Try the demo (5 minutes)

```bash
leadpipe demo
leadpipe -c demo/config.yaml dashboard
```

Open **http://localhost:8787**. You'll see 7 made-up businesses with drafts, replies of every kind
(price question, "not now", unsubscribe, booked call, "let's go ahead") and one client in onboarding.
Click around: approve an email, open a lead, tick an onboarding step. Nothing is ever sent from the demo.

---

## 5. Set it up for your business

```bash
leadpipe init
```

It asks a few questions (your name, service, offer, price range, booking link, address, the area and business types
you want) and creates two files:

- **`config.yaml`**: all settings. Every line has a comment explaining it.
- **`.env`**: passwords and keys. Never share or commit this file.

### 5.1 `config.yaml`: the important parts

**`profile`**: who you are. This is what every email is built from. Be specific and honest.

| Setting | Example | Tip |
|---|---|---|
| `service` | `websites with online booking` | the words a shop owner would use, not "digital solutions" |
| `starter_offer` | `a booking-ready one-page website in 5 days` | small, quick, low risk: easy to say yes to |
| `price_range` | `₹8,000 to ₹15,000` | only used when someone asks "how much?" |
| `proof` | `built booking websites for 12 local clinics` | a real result, written as "built…/helped…". Leave `[]` if you have none yet |
| `proof_by_category` | `{dentist: "built sites for 5 dental clinics"}` | relevant proof beats impressive proof |
| `quick_win` | `a free 2-minute video audit of your website` | the "extra effort" you offer to start a conversation |
| `booking_link` | `https://cal.com/you/15min` | every positive reply pushes here |
| `physical_address` | your real address | added to every outreach email (legal requirement) |

**`targets`**: where to look. You can have several:

```yaml
targets:
  - name: "pune-dentists"
    area: "Pune, India"
    categories: ["dentist", "clinic"]
    timezone: "Asia/Kolkata"
    country: "IN"
    limit: 150
  - name: "london-cafes"
    area: "London, United Kingdom"
    categories: ["cafe", "restaurant"]
    timezone: "Europe/London"
    country: "GB"
    limit: 100
```

`leadpipe categories` lists the business types you can use (dentist, cafe, salon, gym, lawyer, real_estate...).
Anything else can be given as an OpenStreetMap tag like `"shop=bicycle"`.

**`signals`**: what makes a business a good lead **for your service**. Weight 0 = ignore. Higher = more important.
A web designer cares about `no_website`, `not_mobile_friendly`, `no_online_booking`. An ads freelancer cares about
`running_ads` and `no_analytics`. An automation freelancer cares about `hiring` and `no_live_chat`.
Run `leadpipe signals` to see all of them with explanations.

**`sending`**: limits. The defaults are safe: max 40/day, warm-up from 5/day, 10 new leads/day, office hours
9:30–17:30 Mon–Fri in *their* timezone, 3–9 minutes between emails.

**`approval`**: `autopilot_cold: false` and `autopilot_replies: false` mean every email waits for you. Keep them off for
the first 1–2 weeks.

**`sequences`**: the follow-up tracks (see [section 11](#11-the-follow-up-logic-every-path)). You can change the days.

**`onboarding`**: your contract link, payment link, welcome-document link, portal link, and the list of accesses
you need from new clients.

**`llm`**: AI. `auto` uses the Claude API if `ANTHROPIC_API_KEY` is set, else the `claude` command-line tool if you're
logged in (uses your Claude Pro/Max plan), else the built-in templates.

### 5.2 `.env`: passwords

```ini
FROM_EMAIL=asha@getpixelstudio.com
FROM_NAME=Asha Patel
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USER=asha@getpixelstudio.com
SMTP_PASSWORD=your-16-letter-app-password
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=asha@getpixelstudio.com
IMAP_PASSWORD=your-16-letter-app-password
ANTHROPIC_API_KEY=            # optional
DASHBOARD_PASSWORD=           # required if the dashboard is reachable from the internet
```

| Provider | SMTP | IMAP |
|---|---|---|
| Google Workspace / Gmail | `smtp.gmail.com` 587 starttls | `imap.gmail.com` 993 |
| Microsoft 365 | `smtp.office365.com` 587 starttls | `outlook.office365.com` 993 |
| Zoho Mail | `smtp.zoho.in` (or `.com`) 465 ssl | `imap.zoho.in` 993 |

### 5.3 Check everything

```bash
leadpipe doctor
```

Fix every **FAIL** before going live. **WARN** items are worth fixing; **INFO** is just information.

Optional: `leadpipe train https://your-site.com` reads your own website with AI and suggests the `profile` section.

---

## 6. Your first week

The system starts in **dry-run** mode: it does everything except actually sending. Use this week to trust it.

| Day | Do | Command / place |
|---|---|---|
| **1** | Install, `init`, `doctor`, pick signals | `leadpipe init`, `leadpipe doctor`, `leadpipe signals` |
| **1** | Find and research leads | `leadpipe discover` then `leadpipe enrich` (or just `leadpipe start`) |
| **2–3** | **Only look.** Read the leads and drafts. Is the research right? Do the emails sound like you? Edit `profile`, signals, `min_score` until they do | Dashboard → Leads, Outbox; dry-run emails in `data/dry_run/` |
| **4–7** | Switch to real sending (section 13). Approve **10–20 emails a day by hand**, edit anything that sounds off | Dashboard → Outbox |
| **Week 2+** | When you trust the drafts, consider autopilot for outreach. You can turn it off in one click | Settings → Autopilot |

---

## 7. Your daily routine (15 minutes)

1. **Open the dashboard → Overview.** Look at *Waiting on you*.
2. **Replies first** (red badge on Outbox / Replies). These are people who answered. Read their message (click the company),
   check the drafted answer, edit if needed, **Approve**. Hot replies also come to your phone if alerts are set up.
3. **Outbox**: read each outreach email as if *you* were the business owner. Approve, edit, *Rewrite* (new version),
   *Skip this step*, or *Stop emailing them*. If all look good: **Approve all passing outreach**.
4. **Manual DMs**: businesses without email. Copy the message, send it on Instagram/WhatsApp/phone, click **I sent it**.
5. **Calls**: when someone books, prepare (open their lead page: the problems found are your talking points).
   After the call, move them to *proposal* (or it happens automatically the day after the booking).
6. **When someone says yes**: open the lead → **Mark as WON** (section 12).
7. Once a week: **Analytics**. Which step gets replies? Which problems? Which business types? Adjust signals and targets.

---

## 8. The dashboard, page by page

Start it with `leadpipe start` (engine + dashboard) or `leadpipe dashboard` (dashboard only). Open http://localhost:8787.

- **Top bar**: engine status (green = running), `DRY RUN`/`LIVE`, `AUTOPILOT`, and the **Pause all sending** button (emergency stop).
- **Overview**: today's numbers, what's waiting on you, 30-day results, the pipeline by stage, live activity,
  and *Run now* buttons (find businesses, research, write drafts, check replies).
- **Outbox**: every email waiting for approval, replies first. Edit subject/body in place. Problems found by the checks
  are listed under each email (⛔ blocks sending, ⚠️ is advice). Below: approved emails waiting for their send window
  (you can *Un-approve*).
- **Replies**: every reply, what it means (*Asked about price*, *Not now*, *Unsubscribe*...), and the lead's new stage.
- **Manual DMs**: messages for leads without email. Send them yourself, click *I sent it*.
- **Leads**: every company found, its score, the problems found, status (qualified / disqualified and why).
- **Pipeline**: every contact by stage, which follow-up track it's on, when the next action happens.
- **Lead page** (click any company): research, full conversation, history, and actions:
  - *Move this lead* to any stage (this starts that stage's follow-up track)
  - **Mark as WON** → starts onboarding
  - *Log their reply* (for phone / WhatsApp / DM answers): it's understood and handled like an email reply
  - *Write your own email* (goes to the Outbox)
  - *Do not contact (ever)*
  - Onboarding checklist (for clients)
- **Clients**: every won deal with onboarding progress and next step.
- **Analytics**: reply rate per follow-up step, per problem (signal), per business type, bounce rate.
- **Settings**: autopilot switches, your profile and targets, do-not-contact list.

---

## 9. How leads are found and qualified

**Finding** (`discovery`): for each target, OpenStreetMap is searched for the business types in the area (free, no
account). Coverage is good for clinics, cafés, restaurants, gyms, salons and shops; weaker for some trades. If it's thin
for your niche:
- add `google_places` to `discovery.providers` and put `GOOGLE_PLACES_API_KEY` in `.env` (Google bills per search), or
- **import a CSV** from Apollo, LinkedIn Sales Navigator, Clay, Hunter or a Google Sheet:
  ```bash
  leadpipe import leads.csv --target pune-dentists
  ```
  Recognised columns (names are matched loosely): `company`, `website`, `name` (or `first name` + `last name`),
  `title`/`role`, `email`, `phone`, `city`, `category`, `country`, `instagram`, `notes`.

New searches only run when fewer than `discovery.min_ready_leads` fresh leads are waiting, so you don't pile up
thousands of untouched leads.

**Researching** (the Auditor, no AI cost): opens the homepage plus up to 4 useful pages (contact, about, team, careers)
and records **signals with evidence** (e.g. *"Outdated website: © 2017"*). It also finds email addresses (including
`info [at] site [dot] com` and Cloudflare-protected ones) and decision-makers (Founder, Owner, Director, Dr. ...).

**Verifying**: syntax → disposable/placeholder domains → DNS mail server (MX) → role inbox check. Statuses:
`valid` (sent), `risky` = info@/contact@ (sent by default, fine for small businesses), `guess` (not sent unless
`allow_guessed_emails: true`), `invalid` (never). Optional: `HUNTER_API_KEY` for deeper checks.

**Scoring**: sum of the weights of the signals found, +10 if a named decision-maker was found, +5 for a personal
verified address. Leads below `enrichment.min_score` are *disqualified* (with the reason shown). Max 2 contacts per company.

**Enrolling**: every 10 minutes, the best-scoring qualified leads enter the **cold** track, up to
`sending.new_leads_per_day` per day.

---

## 10. How emails are written and checked

Each email follows the formula from the playbooks:

> **Their name → one specific observation about their business → what it costs them (in plain words) →
> one line of relevant proof → one quick win → one soft yes/no question.** Under ~80 words. No pitch, no price, no links in
> the first email.

Example (template, no AI):

> **Subject:** Quick question about Smile Dental Care
>
> Hi Dr Sharma,
>
> I couldn't find a way to book online on smiledental.in. It looks like it's phone or WhatsApp only. People who look you up
> late at night usually book wherever it takes one tap.
>
> I've built booking websites for 12 local clinics, so I see this a lot. I help businesses like yours fix exactly this,
> usually with a 'Book now' button connected to your calendar.
>
> Would it be useful if I sent you a free 2-minute video audit of your website?
>
> Asha Patel · Pixel Studio
> 12 MG Road, Pune 411001, India
> P.S. If this isn't relevant, just reply "no thanks" and I won't email again.

**With AI** (Claude): the same research and rules are given to Claude, which writes a more natural, specific email and
sees the conversation so far (so follow-ups never repeat themselves). If the AI draft fails a check, it gets one retry
with the reasons; if it fails again, the template is used. The AI is told to **never invent** facts, numbers or compliments.

**Checks on every email** (⛔ = blocked until you fix it):

| Check | Level |
|---|---|
| Unfilled placeholders like `[Name]`, `{{first_name}}` | ⛔ |
| Spam / AI-sounding phrases ("Dear Sir/Madam", "I hope this finds you well", "just checking in", "synergy", "game-changer"...) | ⛔ |
| Price in a first email | ⛔ |
| Too many links (0 in a first email, 1 in follow-ups) | ⛔ |
| First email pretending to be a reply (`Re:`/`Fwd:`) | ⛔ |
| Far too long | ⛔ (slightly long = ⚠️) |
| Company name missing, no question at the end, long subject, CAPS, "!!!" | ⚠️ |
| At send time: do-not-contact list, undeliverable address, lead already moved on, daily cap, office hours | automatic |

Signature, postal address and opt-out line are **added at send time**. Never type them into drafts.

---

## 11. The follow-up logic (every path)

Every contact is in one **stage**. Each stage has its own **track** of follow-ups. When a reply arrives, the old track
stops and the new stage's track starts. That's how "if converted → different follow-up, if not → different
follow-up, close after a certain time" works.

### 11.1 No reply yet: the **cold** track

| Day | Email | Idea |
|---|---|---|
| 0 | **intro** | observation + cost + quick-win offer + soft question |
| 3 | **bump** (same thread) | 1–2 lines, one new small detail |
| 7 | **insight** | what you'd change first and why |
| 12 | **value** | "reply yes and I'll make the free audit/mock-up for you" |
| 18 | **breakup** | polite close, door left open |
| 25 | *(no email)* | still silent → stage **lost** (`no response`) |

Days are counted from the previous email, so if sending is delayed (caps, weekends) the spacing stays the same.

### 11.2 What each reply does

| They say (examples) | Understood as | Stage → track | Drafted answer | Alert |
|---|---|---|---|---|
| "Interested", "yes, send it", "tell me more" | interested | **interested** → warm | thanks + 15-min call link | 🔥 |
| "How much?" | price question | **interested** → warm | price *range*, starter offer, call for exact quote | 🔥 |
| "Send your portfolio" | portfolio request | **interested** → warm | proof links + call | 🔥 |
| "How long does it take?" | timeline question | **interested** → warm | delivery time + call | 🔥 |
| "I booked a slot for Thursday" | meeting booked | **meeting** → proposal track starts the day after | confirmation | 🔥 |
| "Send us a quote/proposal" | proposal request | **proposal** → proposal track | "sure, quick call first?" (you write the proposal) | 🔥 needs you |
| "Let's go ahead", "send the agreement" | ready to buy | **proposal** (chasers stopped) | thanks + next steps | 🔥 **Mark as WON** |
| "Talk to our manager ravi@…" | referral | **interested** + new contact for Ravi enters cold track with "X suggested I reach out" | thanks | 🔥 needs you |
| "Not right now", "next quarter", "in 2 months" | not now | **nurture**, first check-in after the time they said (default 60 days) | "may I check back?" | — |
| "We already have an agency" | already have | **nurture** (first check-in after 90 days) | respectful, offer a second opinion | — |
| "Not interested", "no thanks" | not interested | **lost** + do-not-contact | one short "understood, thanks" (needs approval) | — |
| "Unsubscribe", "stop", "remove me" | unsubscribe | **unsubscribed** + do-not-contact | none | — |
| Out-of-office auto-reply | auto reply | unchanged; next follow-up pushed back 4 days | none | — |
| Delivery failure | bounce | **bounced**, address marked invalid | none | — |
| Anything else / unclear | question / other | **interested**, flagged "needs you" | holding reply / AI draft | 🔥 |

**Company rule:** when anyone at a company replies, cold emails to their colleagues stop.

### 11.3 The other tracks

| Track | When | Steps | When it ends without a reply |
|---|---|---|---|
| **warm** | interested, no call yet | day 2 booking link · day 5 proof · day 10 close the loop | → nurture |
| **proposal** | after the call / proposal asked | day 2 "anything to change?" · day 5 FAQ · day 10 smaller first step · day 21 close file | → nurture |
| **nurture** | not now / already have someone | day 30 check-in · day 60 new idea · day 90 last check-in | → lost |
| **client_care** | onboarding finished | day 14 check-in · day 30 testimonial · day 60 referral ask · day 120 next project | stays client |

You can change any day, add or remove steps in `config.yaml → sequences`. Available step types (`purpose`):
`intro, bump, insight, value, breakup, booking_nudge, proof, close_loop, proposal_followup, objection_preempt,
decision_nudge, close_file, nurture_value, nurture_idea, nurture_final, care_checkin, care_feedback, care_referral, care_next`.

### 11.4 Moving leads yourself

Phone call went well? Met them at an event? Open the lead → **Move this lead** (or `leadpipe stage <id> <stage>`).
The new stage's track starts immediately. Stages: `new, cold, interested, meeting, proposal, nurture, won, client,
lost, unsubscribed, bounced, paused`.

---

## 12. When a lead says YES: onboarding

Click **Mark as WON** on the lead page (or `leadpipe won <contact_id> --value 12000`). From the *Client Onboarding
System*: **the sale isn't the start of delivery: remove the chaos before it starts.**

| Step | Email drafted automatically | You tick when it happens | Reminder if stuck |
|---|---|---|---|
| 01 Contract | "Next steps: agreement" with your e-sign link (or "attached") | **Contract signed** | every 2 days, max 3 |
| 02 Payment | invoice / payment link | **Payment received** | every 2 days, max 3 |
| 03 Welcome document | what happens next, what you need, timeline, where we talk + kickoff booking link | — | — |
| 04 Kickoff call | — | **Kickoff call done** | kickoff booking reminder |
| 05 Access | list of accesses (from `onboarding.access_items`), "invite me as a user, never send passwords on chat" | **All access received** | every 2 days, max 3 |
| 06 Client portal | "everything in one place: <link>" | **Client portal live** | — |
| Done | client moves to **client care** (check-in, testimonial, referral, next project) | | |

Rules built in: **no payment, no onboarding**. The welcome email is only drafted after you tick *Payment received*.
Templates for the contract, proposal, welcome document, kickoff agenda, access checklist and portal are in
[docs/templates/](templates/).

CLI: `leadpipe onboard <contact_id>` shows the checklist; `leadpipe onboard <contact_id> payment_received` ticks a step.

---

## 13. Going live: real emails

1. `leadpipe doctor` shows no **FAIL**.
2. You've reviewed dry-run drafts for a few days and like them.
3. In `config.yaml` set `sending.mode: smtp` and (if you know it) `sending.warmup.start_date` to the day you started cold sending.
4. Restart (`Ctrl+C`, then `leadpipe start`). The top bar shows **LIVE**.
5. Keep approving by hand for the first week. Watch **bounce rate** (keep it under 3%) and replies.

**Emergency stop:** dashboard → *Pause all sending*, or `leadpipe sending pause`. Resume with `leadpipe sending resume`.

**Autopilot** (only when you trust the drafts): Settings → *Autopilot for outreach*. Emails that pass every check go
out without you. *Autopilot for simple replies* answers interested / price / timeline / portfolio / booked automatically;
everything else (ready to buy, proposals, referrals, unclear) **always** waits for you.

---

## 14. Running 24/7

The engine has to be running for anything to happen automatically. Pick one:

**A. Your own computer (simplest):** `leadpipe start`. Leave the terminal open and the computer awake. If it sleeps,
nothing breaks: the engine just picks up where it left off.
On Windows, double-click `deploy\start-windows.bat` (restarts itself if it stops). To start it at logon:
Task Scheduler → *Create Task* → Trigger *At log on* → Action *Start a program* → that `.bat` file.

**B. A small cloud server (recommended for true 24/7):** any $4–6/month VPS (Hetzner, DigitalOcean, Lightsail, Oracle free tier).

```bash
sudo apt update && sudo apt install -y python3-venv git
git clone https://github.com/NandaniRamoliya/LeadAutomationPipeline.git && cd LeadAutomationPipeline
python3 -m venv .venv && . .venv/bin/activate && pip install -e .
leadpipe init && nano .env && leadpipe doctor
sudo cp deploy/leadpipe.service /etc/systemd/system/     # edit User= and paths inside first
sudo systemctl daemon-reload && sudo systemctl enable --now leadpipe
journalctl -u leadpipe -f                                 # live logs
```

To open the dashboard from your laptop safely: `ssh -L 8787:127.0.0.1:8787 you@server` then visit http://localhost:8787.
Or put it on a domain with HTTPS (section 15).

**C. Docker:**

```bash
leadpipe init            # or copy config.yaml and .env next to docker-compose.yml
# set DASHBOARD_PASSWORD in .env (the container listens on 0.0.0.0 internally)
docker compose up -d
docker compose logs -f
```

Data (the database and dry-run emails) lives in `./data`. **Back it up**: it's a single SQLite file, `data/leadpipe.db`.

Only run **one** engine per database (a lock file prevents accidents).

---

## 15. Unsubscribe links and calendar bookings (public URL)

Without a public URL, opt-out works by reply ("no thanks" / "unsubscribe"), which is understood automatically. With one,
you also get a one-click **List-Unsubscribe** header (Gmail's "Unsubscribe" button) and automatic booking detection.

1. Point a subdomain (e.g. `leads.yourdomain.com`) to your server and run Caddy with `deploy/Caddyfile.example` (free HTTPS).
2. `config.yaml`: `dashboard.public_base_url: "https://leads.yourdomain.com"`.
3. `.env`: `DASHBOARD_PASSWORD=…` (the dashboard is then password-protected; the unsubscribe page and webhook stay public).
4. **Calendar webhook**: set `BOOKING_WEBHOOK_TOKEN=some-long-random-text` in `.env`, then add a webhook in your calendar tool:
   - Cal.com: Settings → Developer → Webhooks → URL `https://leads.yourdomain.com/webhooks/booking?token=…`, event *Booking created*.
   - Calendly (paid plans): Integrations → Webhooks, same URL, event `invitee.created`.

   When a lead books, they move to **meeting** automatically and you get an alert.

---

## 16. Alerts on your phone

Hot replies, booked calls and a **daily summary** (at `notifications.daily_digest_hour`) can be sent to:
- **Email**: `notifications.owner_email` in `config.yaml` (use your normal address, not the sending one).
- **Telegram**: create a bot with @BotFather, put `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.
- **Slack / Discord**: an incoming webhook URL in `NOTIFY_WEBHOOK_URL`.

---

## 17. Command reference

| Command | What it does |
|---|---|
| `leadpipe init` | create `config.yaml` + `.env` (asks questions) |
| `leadpipe doctor` | check settings, mailbox logins, SPF/DKIM/DMARC, AI |
| `leadpipe demo` | safe demo with made-up leads |
| `leadpipe train <url>` | AI suggests your profile from your website |
| `leadpipe signals` / `categories` | explain signals / list business types |
| `leadpipe start` | **engine + dashboard (24/7)** |
| `leadpipe run` / `run --once` | engine only / one cycle |
| `leadpipe dashboard` | dashboard only |
| `leadpipe discover [--target NAME]` | find businesses now |
| `leadpipe import file.csv [--target NAME]` | import leads |
| `leadpipe enrich` | research websites now |
| `leadpipe leads` / `contacts [--stage X]` | list companies / contacts |
| `leadpipe write` | enroll leads + write due drafts now |
| `leadpipe outbox` | list drafts; `outbox show ID`, `approve ID` (`approve` alone = all passing), `reject ID`, `skip ID`, `stop ID` |
| `leadpipe send [--now]` | send due approved emails now (`--now` ignores office hours/gap, caps still apply) |
| `leadpipe inbox` | check replies now |
| `leadpipe stage ID STAGE` | move a contact |
| `leadpipe won ID [--value N]` | deal won → onboarding |
| `leadpipe onboard ID [STEP] [--undo]` | show / tick onboarding steps |
| `leadpipe sending pause` / `resume` | emergency stop |
| `leadpipe autopilot on/off [--replies]` | approval-free sending |
| `leadpipe stats` | reply rates, funnel, per step |
| `leadpipe export [--stage X] [--file out.csv]` | export contacts |
| `leadpipe suppress email@x.com` / `@domain.com` | never email them |
| `leadpipe forget email@x.com` | delete all data about a person (erasure request) |
| `leadpipe simulate-reply ID "text"` | test how a reply would be handled |

To run a second, separate setup (another niche or mailbox), give it its own folder with its own `config.yaml` and `.env`, and use `leadpipe -c that-folder/config.yaml ...`. Its database lives in `that-folder/data/`.

---

## 18. Troubleshooting

| Problem | Fix |
|---|---|
| `command not found: leadpipe` | Activate the venv: `source .venv/bin/activate` (Windows `.venv\Scripts\activate`) |
| `SyntaxError` right after install on a Mac | The venv was built with Python 3.9. Delete `.venv`, recreate with `python3.13 -m venv .venv` |
| Dashboard says *Engine stopped* | Run `leadpipe start` (or `run`). Check the terminal / `journalctl -u leadpipe` for errors |
| Nothing is being sent | Check in this order: top bar not *SENDING PAUSED*; emails are **approved** (Outbox → bottom list); it's inside office hours in *their* timezone, Mon–Fri; today's cap not reached (Overview); `sending.mode` is `smtp` |
| SMTP login failed | Gmail/Workspace needs an **App Password**, not your normal password; 2-Step Verification must be on |
| Emails land in spam | Almost always the domain: SPF/DKIM/DMARC (`leadpipe doctor`), finish warm-up, halve the volume, fewer links, plainer text |
| Discover finds nothing | Use "City, Country" for `area`; try other categories (`leadpipe categories`); OpenStreetMap is weak for some trades: use Google Places or CSV import |
| Most leads *disqualified* | See the reason on the Leads page. Lower `enrichment.min_score` or raise weights of signals that matter to you |
| Replies aren't detected | Check `IMAP_*` in `.env` (`leadpipe doctor`). Gmail: IMAP must be enabled in Gmail settings. Use *Log their reply* for phone/DM answers |
| AI not used | `leadpipe doctor` → AI section. Set `ANTHROPIC_API_KEY`, or install + `claude login`, and `llm.provider: auto` |
| Bounce rate above 5% | Pause sending. Set `allow_role_emails: false` and `allow_guessed_emails: false`; consider `HUNTER_API_KEY` |
| "Another engine is already running" | Only one engine per database. Stop the other one, or delete `data/engine.lock` if it crashed |
| Dashboard "Form expired" | Reload the page and try again |

The **Activity** box on the Overview page and the lead's **History** show exactly what happened and why.

---

## 19. FAQ

**Will it send anything without me?** No. Dry-run is the default, and even in live mode every email waits in the Outbox
until you approve it, unless you turn on autopilot yourself.

**How many emails per day is safe?** Per mailbox: start at 5, grow to 30–50 after a month of good results. For more
volume, add more mailboxes/domains (run a second copy with its own config), don't push one mailbox harder.

**What reply rate should I expect?** Well-targeted, personal emails: 5–15% replies, 2–5% positive. If you're below 2%
after 100 emails, the targeting or the offer is wrong. Check Analytics → signals and categories.

**Does it work for clients abroad?** Yes. Add a target per country with the right timezone. Check that country's rules
first ([LEGAL_AND_COMPLIANCE.md](LEGAL_AND_COMPLIANCE.md)); some (e.g. Germany, Canada) are strict.

**Can I use LinkedIn / Instagram?** The pipeline drafts messages for leads without email and tracks the follow-ups
(Manual DMs page), but **you** send them. Automating LinkedIn/Instagram breaks their terms and gets accounts banned.

**Where is my data?** Only on your machine/server in `data/leadpipe.db`. With AI on, the lead's public website text and
the email thread are sent to Claude to write/understand emails.

**How much does AI cost?** It depends on how many follow-ups each lead gets: one research summary, up to five drafts and
reading replies. Expect cents per lead, not dollars. For lower cost, set a smaller model in `llm.model` (e.g. `claude-haiku-5-5`).
Using the `claude` CLI with a Pro/Max plan uses your plan's limits instead of per-token billing.
