# User Guide: LeadAutomationPipeline

This guide explains in simple words **what the system does, how it works, how to set it up, and how to use it
every day**. No programming knowledge is needed beyond copying a few commands.

**Contents**

1. [What it does](#1-what-it-does)
2. [The 5 pages of the dashboard](#2-the-5-pages-of-the-dashboard)
3. [Install (10 minutes)](#3-install-10-minutes)
4. [Try the demo](#4-try-the-demo)
5. [Set it up for your business](#5-set-it-up-for-your-business)
6. [Make it run 24/7 by itself](#6-make-it-run-247-by-itself)
7. [Your daily routine (15–30 minutes)](#7-your-daily-routine-1530-minutes)
8. [Where leads come from (start local, then the world)](#8-where-leads-come-from-start-local-then-the-world)
9. [How each business is researched](#9-how-each-business-is-researched)
10. [Channels: email, WhatsApp, Instagram, LinkedIn, X, Facebook, calls](#10-channels-email-whatsapp-instagram-linkedin-x-facebook-calls)
11. [The follow-up logic (every path)](#11-the-follow-up-logic-every-path)
12. [When a lead says YES: onboarding](#12-when-a-lead-says-yes-onboarding)
13. [Going live with real emails](#13-going-live-with-real-emails)
14. [Sending more: extra mailboxes](#14-sending-more-extra-mailboxes)
15. [Importing your own lists (Google Sheets / CSV)](#15-importing-your-own-lists-google-sheets--csv)
16. [Alerts on your phone](#16-alerts-on-your-phone)
17. [Command reference](#17-command-reference)
18. [Troubleshooting](#18-troubleshooting)
19. [FAQ](#19-faq)

---

## 1. What it does

It is a small sales team that never sleeps:

| Step | What happens | Who |
|---|---|---|
| **Find** | Searches one area for the business types you chose (e.g. dentists, cafés, gyms in Surat). When an area is finished, it moves to the next city, then the next country. | automatic |
| **Research** | Opens each business's website and notes real problems you can fix (no online booking, not mobile-friendly, slow, "Not secure", no website at all, low Google rating…). Finds the owner's name, email, phone, WhatsApp, Instagram, LinkedIn. | automatic |
| **Score** | Keeps only businesses with problems your service fixes. High-value leads you import go first. | automatic |
| **Write** | Writes a short, personal message for each person, built on *their* problem. With Claude (optional) the writing is even more natural. | automatic |
| **Email** | Sends emails one at a time, in *their* office hours, within safe daily limits. | automatic (after your OK, or autopilot) |
| **WhatsApp / Instagram / LinkedIn / X / Facebook / calls** | Writes the message or call script and puts it on your **To-do** with a one-click button. | **you** press send / dial (see §10 why) |
| **Follow up** | Follows up on a schedule. Every follow-up adds something new. | automatic |
| **Read replies** | Reads every email reply, understands it (interested? price? not now? stop?), drafts the answer, moves the lead to the right track. | automatic |
| **Close** | No reply after the whole track → closed politely. "Not now" → checks back in 1–3 months. "Stop" → never contacted again. | automatic |
| **Onboard** | When someone says yes: contract → payment → welcome → kickoff → access → portal, then client care and referrals. | automatic messages, you tick what happened |

**What you do:** spend 15–30 minutes a day on the **To-do** page (answer replies, send the WhatsApp/DM tasks,
make the calls), take the meetings, and do the work.

---

## 2. The 5 pages of the dashboard

Open **http://localhost:8787** (or run `leadpipe open`).

| Page | What it's for |
|---|---|
| **Home** | Is it running? What happened today? Which city is being searched now, and which come next? A big **Pause everything** button and a **Run in background always** button. |
| **To-do** | The *only* page you need every day. Everything that needs you, most important first, each with one button: **Send reply**, **Open WhatsApp (message ready)**, **📞 Call** + script + "what happened", **Approve** emails, client steps. |
| **Leads** | Every business: why it's a good lead, its status in plain words (Reaching out / Talking / Clients / Later / Closed) and *what happens next* ("Email 3 of 5 · in 2 days"). Click one to see everything about it. |
| **Clients** | People who said yes, and how far their onboarding is. |
| **Settings** | **Setup** form (your business, who/where to reach, channels, your mailbox), **Where to reach** (the city list and progress), **Automation & 24/7**, **Import leads**, **Do-not-contact**, and **What's working** (statistics). |

Top right: **Running** (green) / **Not running** / **PAUSED**, and **TEST MODE** (nothing really sent) or **LIVE**.

---

## 3. Install (10 minutes)

You need **Python 3.10 or newer** ([python.org](https://www.python.org/downloads/); on Windows tick *"Add python.exe to PATH"*).

**Windows** (PowerShell):

```powershell
git clone https://github.com/NandaniRamoliya/LeadAutomationPipeline.git
cd LeadAutomationPipeline
py -3 -m venv .venv
.venv\Scripts\activate
pip install -e .
```

**Mac / Linux**:

```bash
git clone https://github.com/NandaniRamoliya/LeadAutomationPipeline.git
cd LeadAutomationPipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Check: `leadpipe --help`. Every time you open a new terminal, first `cd LeadAutomationPipeline` and activate
(`.venv\Scripts\activate` or `source .venv/bin/activate`).

---

## 4. Try the demo

```bash
leadpipe demo
leadpipe -c demo/config.yaml dashboard
```

Open http://localhost:8787. Made-up businesses with every situation: replies of each kind, a WhatsApp task, a call with
script, a client in onboarding, the city rollout. Click everything. Nothing is ever sent from the demo.

---

## 5. Set it up for your business

```bash
leadpipe start
```

Open http://localhost:8787 → **Settings → Setup** and fill in the form (2 minutes). It saves everything for you
(to `config.yaml` and `.env`) and the running system uses it within a minute.

| Section | Fill in | Tips |
|---|---|---|
| **1. Your business** | name, what you sell, small first offer, price range, free quick win, booking link, postal address, proof | Write what you sell the way a shop owner would say it. Proof = a real result ("built booking sites for 12 clinics"). Price is only used when someone asks. |
| **2. Who to reach** | business types, start city, your timezone, next areas, countries | Start where you are. It moves on automatically. |
| **3. Channels** | tick the channels you'll use, tasks per day | Fewer, better calls/DMs beat many. |
| **4. Your email** | sending mailbox and password | See §13 before switching to LIVE. |
| **5. Extras** | AI, Serper/Google keys, alerts | All optional. |

Then check everything: `leadpipe doctor` (fix every FAIL before going live).

> Prefer the terminal? `leadpipe init` asks the same questions, and every setting is explained inside `config.yaml`.

---

## 6. Make it run 24/7 by itself

Run **once** (or click **Run in background always** on the Home page):

```bash
leadpipe service install
```

| Your computer | What it does |
|---|---|
| **Windows** | Creates a Task Scheduler task "LeadPipeline": starts when you log in, runs hidden (no window), keeps the PC awake while working, runs on battery, and is re-checked every 5 minutes, so if it ever stops it restarts by itself. |
| **Mac** | A LaunchAgent that starts at login and is kept alive. |
| **Linux** | A systemd user service with automatic restart (`sudo loginctl enable-linger $USER` keeps it running after logout). |

Check it: `leadpipe service status` · see the log: `leadpipe service logs` · pause: `leadpipe service stop` ·
resume: `leadpipe service start` · remove: `leadpipe service uninstall`.

**Important:** a program can't run while the computer is **shut down**. Leave it on (sleep is blocked while it
works; also set *Power & battery → When plugged in, put my device to sleep → Never*, and on laptops *Lid close → Do nothing*).
For **true 24/7 even when your laptop is off**, run it on a small cloud server (~$5/month: Hetzner, DigitalOcean, AWS Lightsail):

```bash
sudo apt update && sudo apt install -y python3-venv git
git clone https://github.com/NandaniRamoliya/LeadAutomationPipeline.git && cd LeadAutomationPipeline
python3 -m venv .venv && . .venv/bin/activate && pip install -e .
leadpipe init                    # or copy your config.yaml and .env from your PC
leadpipe service install && sudo loginctl enable-linger $USER
```

Open its dashboard from your PC through an SSH tunnel: `ssh -L 8787:127.0.0.1:8787 you@server`, then http://localhost:8787.
Or use Docker: `docker compose up -d` (set `DASHBOARD_PASSWORD` in `.env`).

Only **one** copy runs per database. Starting a second one just says "already running".

---

## 7. Your daily routine (15–30 minutes)

Open the dashboard → **To-do**, top to bottom:

1. **Replies & client messages**: read what they wrote, check the drafted answer, **Send reply**. Answer within a few hours.
2. **Your decision needed**: "let's go ahead" → open the lead → **They said YES** (starts onboarding). Proposal requests, unclear replies.
3. **Calls to make**: tap **📞 Call**, read the script, then press what happened: *No answer* (tried again tomorrow), *Left voicemail*, *Talked: interested*, *Talked: booked*, *Call back later* (≈ a month), *Not interested*, *Wrong number*.
4. **Messages to send**: **Open WhatsApp (message ready)**, press send in WhatsApp, come back, **✓ I sent it**. For Instagram/LinkedIn/X/Facebook the message is copied and the profile opens: paste, send, **✓ I sent it**.
5. **Emails waiting for your OK**: read a few, then **Approve all**. (After 1–2 weeks of good drafts, turn on *Autopilot* in Settings → Automation and this section disappears.)
6. **Client steps**: "Did they sign / pay / did the kickoff happen?" → **Yes, done**.

If someone answers on WhatsApp, Instagram or by phone, open the lead → **They answered by WhatsApp / phone / DM?** →
paste what they said → **Log it**. It's understood and handled exactly like an email reply.

Once a week: **Settings → What's working**: which follow-up gets replies, which problems and business types respond.

---

## 8. Where leads come from (start local, then the world)

**Areas, one at a time.** You set a start area ("Surat, India") and business types. The system:

1. Finds the area on the map and splits big cities into **9 pieces** (so it finds *everyone*, not just the first results).
2. Searches one piece at a time, only when fewer than `min_ready_leads` fresh leads are waiting (so you never pile up thousands of untouched leads).
3. When all pieces are done, marks the area **finished** and moves on: your *next areas* → the rest of your country
   (Mumbai, Delhi, Bengaluru, Ahmedabad… 45 Indian cities) → the next countries (UAE, UK, USA, Australia, Canada, Singapore…), city by city.

See and steer it in **Settings → Where to reach**: *Do this area next*, *Skip*, *Search again*. Each area gets its own
timezone, so emails always arrive in **their** office hours. Countries in *Never search in* are skipped.

**Sources:**

| Source | Cost | Good for |
|---|---|---|
| OpenStreetMap (default) | free | clinics, cafés, restaurants, gyms, salons, shops. Thin for some trades. |
| Serper (Google Maps results) | 2,500 free searches, then ~$1/1,000 | everything on Google Maps, with ratings & reviews. Add `SERPER_API_KEY` in Setup. |
| Google Places | paid by Google | best coverage. Add `GOOGLE_PLACES_API_KEY`. |
| Your own lists | free | Google Sheets / CSV (your Maps scrape, high-value clients, Apollo, Sales Navigator). See §15. |

---

## 9. How each business is researched

The Auditor reads the homepage plus up to 5 useful pages (contact, team, about, careers, blog). No AI cost. It records
**problems with evidence** (shown on the lead page as "Why they're a good lead"):

| Problem | Example evidence |
|---|---|
| No website / Instagram only / site broken | "social media page only", "HTTP 500" |
| No online booking | no booking button or booking tool |
| Not mobile-friendly, slow, "Not secure" | no mobile viewport, 6.2 s, plain http |
| Outdated site, abandoned blog | "© 2017", last post 2021 |
| DIY builder (Wix, GoDaddy…), built by an agency | "Website by XYZ" in the footer |
| Running ads | Google Ads / Meta Pixel found |
| Hiring | open roles from their Greenhouse / Lever / Ashby / Workable board |
| Weak SEO, no structured data, no analytics, no chat button | missing title/description… |
| Low Google rating, few reviews | 3.6★, 9 reviews (from Maps imports / Serper / Google) |
| Startup backed / recently funded | "backed by Y Combinator", "we raised a $2M seed" |

**People and contacts:** owner/founder/director/"Dr." names; emails from the site (including `info [at] site [dot] com`
and hidden ones); the company's **email pattern** (if `nora.vale@` belongs to Nora Vale, the owner Ravi Mehta is
`ravi.mehta@`); phone numbers; WhatsApp, Instagram, LinkedIn, X and Facebook links. Every email is checked (domain can
receive mail, not a no-reply address). Max 2 people per company.

**Score:** sum of the problems' weights (you choose them: Settings or `config.yaml → signals`), +10 if a named
decision-maker, +30 for high-value imports. Below `min_score` → set aside as "not a fit" (Leads → *Being researched / not a fit*).

---

## 10. Channels: email, WhatsApp, Instagram, LinkedIn, X, Facebook, calls

| Channel | Automatic? | How it works |
|---|---|---|
| ✉️ **Email** | **Yes, fully** | Sent from your mailbox, in their office hours, with warm-up and daily limits. Replies are read automatically. |
| 💬 **WhatsApp** | One click | The message is written; **Open WhatsApp** opens the chat with the text already typed; you press send. Used when the business has a mobile number or WhatsApp link. |
| 📸 Instagram · 💼 LinkedIn · 𝕏 X · 📘 Facebook | One click | The message is copied and their profile/DM opens; you paste and send. |
| 📞 **Calls** | One tap | Tap to dial (on your phone, open the dashboard on the same Wi-Fi, see §19), read the personalised script, press what happened. "No answer" retries tomorrow (2 attempts). |

**Why not fully automatic?** Instagram, LinkedIn, X, Facebook and WhatsApp ban accounts that send automated messages
(and WhatsApp's business API forbids cold messages). Automated/robo calls are illegal in many countries (US TCPA, India's
TRAI rules, UK PECR). One click per message keeps your accounts and phone number safe, and personal messages get far more
replies anyway.

**Which channel when:** leads **with email** get the email track, plus (only if they're good leads, score ≥
`min_score_for_tasks`) one call and one DM in between. Leads **without email** get a DM/call track
(WhatsApp first, then Instagram, LinkedIn, Facebook, X). Calls/DMs are capped per day (`tasks_per_day`), best leads first.

---

## 11. The follow-up logic (every path)

Every lead is in one **stage**. Each stage has its own **track**. When they reply, the old track stops and the right
new one starts. That's how "converted → different follow-up, not converted → different follow-up, close after a certain
time" works.

### 11.1 No reply yet

**Has email (`cold`):**

| Day | Step |
|---|---|
| 0 | ✉️ intro: their problem, what it costs, free quick win, one soft question |
| 3 | ✉️ short bump with one new detail (same thread) |
| 5 | 📞 call (good leads only, if there's a phone) |
| 7 | ✉️ new insight: what you'd change first |
| 10 | 💬 short DM on WhatsApp/Instagram/LinkedIn (good leads only) |
| 12 | ✉️ "reply yes and I'll make the free audit/mock-up for you" |
| 18 | ✉️ polite close, door open |
| 25 | still silent → **Closed** |

**No email (`cold_social`):** day 0 DM intro → day 2 call → day 6 DM follow-up → day 13 last DM → day 20 closed.

Days count from the previous message, so delays (limits, weekends) never squash the spacing. Steps that can't happen
(no phone, no social profile) are skipped automatically.

### 11.2 What each reply does

| They say (examples) | Understood as | Next |
|---|---|---|
| "Interested", "yes, send it", "tell me more" | interested | **Talking**: answer drafted (call link); booking nudge day 2, call day 4, proof day 6, close day 10 → Later |
| "How much?" / "Portfolio?" / "How long?" | question | **Talking**: answer drafted (price *range* only when asked) |
| "I booked Thursday" / calendar booking | call booked | **Call booked** → the day after: proposal track (day 2 email, day 4 call, day 6 FAQ, day 10 smaller first step, day 21 close → Later) |
| "Send a quote/proposal" | proposal request | **Deciding** (you write the proposal; template in docs/templates) |
| "Let's go ahead", "send the agreement" | ready to buy | **Your decision needed** → click **They said YES** → onboarding |
| "Not now", "next quarter", "in 2 months" | not now | **Later**: check back at the time they said (default 60 days), then 30/60/90-day check-ins → Closed |
| "We already have someone" | already have | **Later** (first check-in after 90 days) |
| "Talk to our manager ravi@…" | referral | Ravi is added and contacted ("X suggested I reach out") |
| "Not interested", "no thanks" | no | **Closed** + one polite "understood" (needs your OK) + never contacted again |
| "Unsubscribe", "stop", "remove me" | opt-out | **Opted out**, never contacted again (email, phone and profiles) |
| Out-of-office | auto-reply | nothing changes; next follow-up pushed 4 days |
| Delivery failure | bounce | **Email bounced**, address never used again |

When **anyone** at a company replies, outreach to their colleagues stops.

### 11.3 Moving leads yourself

Open a lead → **More actions → Move** (or `leadpipe stage <id> <stage>`). The new stage's track starts right away.

---

## 12. When a lead says YES: onboarding

Open the lead → **More actions → They said YES** (or `leadpipe won <id> --value 15000`).

| Step | Written for you automatically | You tick (To-do → Client steps) | Reminder if stuck |
|---|---|---|---|
| 01 Contract | "Next steps: agreement" with your e-sign link | **Contract signed** | every 2 days, 3× |
| 02 Payment | invoice / payment link | **Payment received** | every 2 days, 3× |
| 03 Welcome | what happens next, what you need, timeline + kickoff link | | |
| 04 Kickoff | | **Kickoff call done** | kickoff link reminder |
| 05 Access | the access list (invite me as a user, never send passwords on chat) | **All access received** | every 2 days, 3× |
| 06 Portal | "everything in one place: <link>" | **Client portal live** | |
| Then | **client care**: check-in day 14, testimonial day 30, referral ask day 60, next-project idea day 120 | | |

No payment, no onboarding: the welcome message only comes after *Payment received*. Templates for the contract, proposal,
welcome document, kickoff agenda, access checklist and portal are in [docs/templates/](templates/).

---

## 13. Going live with real emails

1. **Use a separate sending domain** (e.g. `getpixelstudio.com` if your site is `pixelstudio.com`): ~₹800/year + a
   Google Workspace mailbox (~$7/month). This protects your main domain.
2. **DNS records** at your domain provider: SPF `v=spf1 include:_spf.google.com ~all` (TXT on `@`); DKIM (Google Admin →
   Apps → Gmail → Authenticate email → generate, add TXT `google._domainkey`, start authentication); DMARC TXT on `_dmarc`:
   `v=DMARC1; p=none; rua=mailto:you@yourmaindomain.com`.
3. **App Password**: Google Account → Security → 2-Step Verification → App passwords. Put it in Setup → 4.
4. **Warm up** the mailbox 2–4 weeks with normal use. The system also ramps up by itself: 5 emails on day 1, +2 per day, up to your daily limit.
5. `leadpipe doctor` shows no FAIL (it checks SPF, DKIM, DMARC, SMTP and IMAP login).
6. Settings → Setup → tick **LIVE** → Save. The top bar shows **LIVE**.

| Provider | SMTP | IMAP |
|---|---|---|
| Google Workspace / Gmail | `smtp.gmail.com` 587 starttls | `imap.gmail.com` 993 |
| Microsoft 365 | `smtp.office365.com` 587 starttls | `outlook.office365.com` 993 |
| Zoho Mail | `smtp.zoho.in` (or `.com`) 465 ssl | `imap.zoho.in` 993 |

**Emergency stop:** Home → **Pause everything** (or `leadpipe sending pause`).

**Autopilot** (Settings → Automation): *outreach* emails that pass every check go without your OK; *simple replies*
(interested / price / timeline / portfolio / booked) go by themselves. Ready-to-buy, proposals, referrals and unclear replies
**always** wait for you.

---

## 14. Sending more: extra mailboxes

One mailbox should send **30–50 cold emails a day** at most. To reach more people, add mailboxes (ideally on 1–3 extra
sending domains), not more volume per mailbox. In `config.yaml`:

```yaml
mailboxes:
  - {name: "second", email: "asha@getpixelstudio.co", from_name: "Asha Patel",
     smtp_host: "smtp.gmail.com", smtp_port: 587, smtp_security: starttls,
     imap_host: "imap.gmail.com", password_env: "MAILBOX2_PASSWORD"}
```

and in `.env`: `MAILBOX2_PASSWORD=the-app-password`. Each mailbox warms up separately and has its own daily limit.
A lead always hears from the same mailbox, and replies to every mailbox are read.

---

## 15. Importing your own lists (Google Sheets / CSV)

Settings → **Import leads**, or:

```bash
leadpipe import "https://docs.google.com/spreadsheets/d/…/edit#gid=0" --area "Surat, India" --priority
leadpipe import my-leads.csv --area "Dubai, UAE"
```

- **Google Sheets:** in the sheet, *Share → General access → Anyone with the link → Viewer*. Tick *Keep synced* and
  new rows are imported every 6 hours.
- **Columns are recognised automatically**, including the ones Google Maps scrapers use: `title`/`name`, `website`,
  `phone`, `email`, `address`, `city`, `totalScore`/`rating`, `reviewsCount`/`reviews`, `categoryName`/`category`,
  `instagram`, `facebook`, `linkedin`, `url` (Maps link), plus `priority`/`tier`/`vip` (yes/high/1 = high-value).
- **High-value** leads (`--priority` or a priority column) are researched and contacted first, get calls/DMs even with
  a low score, and show a *high-value* badge.
- Duplicates are merged (same website, Maps id, or name + city), so importing again is safe.
- Phone numbers are normalised (+91…) and mobile numbers are used for WhatsApp.

---

## 16. Alerts on your phone

Hot replies, booked calls and a daily summary can go to:
- **Telegram:** create a bot with @BotFather, put the token and your chat id in Setup → 5.
- **Email:** Setup → 5 → *Send hot-lead alerts to* (your normal address).
- **Slack/Discord:** an incoming-webhook URL in `.env` as `NOTIFY_WEBHOOK_URL`.

---

## 17. Command reference

| Command | What it does |
|---|---|
| `leadpipe start` | run everything (engine + dashboard) |
| `leadpipe service install` / `status` / `stop` / `start` / `logs` / `uninstall` | run 24/7 in the background |
| `leadpipe open` | open the dashboard |
| `leadpipe demo` | safe demo |
| `leadpipe init` / `doctor` | create settings / check everything |
| `leadpipe areas` · `areas add "Pune, India"` · `areas skip "X, Y"` | the city-by-city rollout |
| `leadpipe discover [--area "City, Country"] [--steps 3]` | search now |
| `leadpipe import <file.csv or sheet link> [--area …] [--priority]` | import leads |
| `leadpipe enrich` · `write` · `send [--now]` · `inbox` | run one step now |
| `leadpipe leads` · `contacts [--stage X]` · `stats` | look at the data |
| `leadpipe outbox [show/approve/reject/skip/stop ID]` | review emails in the terminal |
| `leadpipe stage ID STAGE` · `won ID` · `onboard ID [STEP]` | move leads / clients |
| `leadpipe export [--calls] [--file out.csv]` | export contacts, or today's call list for a telecaller |
| `leadpipe sending pause/resume` · `autopilot on/off [--replies]` | switches |
| `leadpipe suppress x@y.com` · `forget x@y.com` | never contact / delete all data about a person |
| `leadpipe simulate-reply ID "text"` | test how a reply is handled |

---

## 18. Troubleshooting

| Problem | Fix |
|---|---|
| `leadpipe` not found | Activate the venv first (`.venv\Scripts\activate` / `source .venv/bin/activate`). |
| Home says **Not running** | `leadpipe start`, or `leadpipe service install`. Check `leadpipe service logs`. |
| Nothing is sent | In order: not *PAUSED*; mode is *LIVE*; emails approved (or autopilot on); inside *their* office hours on a weekday; today's limit not reached. |
| SMTP login failed | Gmail/Workspace needs an **App Password** (2-Step Verification on). |
| Emails land in spam | Fix SPF/DKIM/DMARC (`leadpipe doctor`), finish warm-up, lower the daily limit. |
| No businesses found | Write the area as "City, Country". Try other business types. Add Serper (2,500 free) for trades OpenStreetMap misses. |
| Most leads "not a fit" | Leads → *Being researched / not a fit* shows why. Lower `min_score`, or raise the weights of problems your service fixes. |
| Too many / too few calls & DMs | Settings → Setup → 3: tasks per day, minimum score. |
| Google Sheet import fails | Share it as *Anyone with the link → Viewer*, or download it as CSV and upload. |
| Replies not detected | IMAP host/port/password in Setup → 4 (Gmail: enable IMAP in Gmail settings). Or log replies by hand. |
| Bounce rate above 5% | Pause. In `config.yaml`: `allow_role_emails: false`, `allow_pattern_emails: false`. |
| "Already running" | Only one copy per database. That's normal if the background service is on. |

The **Home → What just happened** list and each lead's **History** show exactly what happened and why.

---

## 19. FAQ

**Will it send without me?** No. It starts in TEST MODE, and in LIVE mode every email waits on your To-do until you
approve it, unless you switch on autopilot.

**Can I use the dashboard on my phone (for calls)?** Yes. In `config.yaml` set `dashboard.host: "0.0.0.0"` and set
`DASHBOARD_PASSWORD` in `.env`, restart, then open `http://<your-computer's-IP>:8787` on your phone (same Wi-Fi).
**📞 Call** buttons dial directly, and **Open WhatsApp** opens the WhatsApp app.

**How many people can it reach?** Email: about 30–50 new + follow-ups per mailbox per day after warm-up (add mailboxes
to grow). Calls/DMs: as many as you set in *tasks per day*. It never stops finding new areas.

**What reply rate should I expect?** Targeted, personal outreach: 5–15% replies, 2–5% interested. Below 2% after 100
people means the targeting or offer needs work (Settings → What's working).

**Is it legal?** Cold B2B outreach is legal in many countries *if you follow the rules*, and the system builds most of
them in. Some countries (Germany, Canada…) are strict. Read [LEGAL_AND_COMPLIANCE.md](LEGAL_AND_COMPLIANCE.md). Not legal advice.

**Where is my data?** Only on your computer/server: `data/leadpipe.db`. Back it up. With AI on, a lead's public website
text and the conversation are sent to Claude to write and understand messages.

**How much does the AI cost?** Optional. With the `claude` command-line tool logged in to your Claude Pro/Max plan it uses
your plan (no per-message bill). With an API key, expect cents per lead. `llm.max_calls_per_day` caps it.
