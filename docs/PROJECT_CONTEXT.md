# Project Context: LeadAutomationPipeline

> **Purpose of this file:** hand it to any new AI conversation (Claude, ChatGPT, …) so it can continue the work
> without starting from the beginning. It summarises what was asked, what was decided and why, what exists now,
> what's left, and how to work on the code. Keep it updated when something important changes.

---

## 0. Paste this at the start of a new conversation

```
I'm continuing work on my project LeadAutomationPipeline
(GitHub: https://github.com/NandaniRamoliya/LeadAutomationPipeline, branch main).
Please read docs/PROJECT_CONTEXT.md first. It explains the goal, decisions, current state and next steps.
Then read docs/DEVELOPER_GUIDE.md for the code and docs/USER_GUIDE.md for how it's used.
Act as an experienced salesperson + freelancer (20+ years) and a senior Python developer.
Keep explanations in simple English. Work directly on the main branch, run the tests before committing.
What I want now: <write your request here>
```

---

## 1. Who and what

- **Owner:** Nandani Ramoliya, freelancer, business name **Indrix** (Surat, India). Sells **AI automation**
  (WhatsApp/email assistants that answer enquiries, book appointments and follow up).
- **Project:** a Python app that runs **24/7 in the background** and does sales outreach automatically:
  find leads → research them → reach out → follow up → handle replies → different follow-ups for converted /
  not-converted leads → close after a set time → onboard clients.
- **Goal in the owner's words:** "Build something that works 24/7 for me… all steps automatic, from finding the right
  leads, reaching out, follow-up, if converted a different follow-up, if not a different follow-up, close follow-up
  after a certain time." Later: "reach every person in the world that we can be helpful to", "start from one place,
  then other areas, the whole world", channels "email, Insta, WhatsApp, Twitter, LinkedIn and call", and
  "it runs automatically in the background every day 24/7 is the main concern".
- **Owner's setup:** Windows PC, project at `D:\LeadAutomationPipeline`, Python venv `.venv`, runs `leadpipe start`;
  the dashboard is at http://127.0.0.1:8787. The `claude` CLI is installed (the app uses it for AI writing on the owner's Claude plan).
- **Communication preferences:** simple English (owner writes informal English/Hinglish); wants complete work "in one
  go"; work on the **main** branch; wants clear briefs before big decisions.

## 2. Source material (4 PDFs the owner provided; rules are built into the code and docs/SALES_PLAYBOOK.md)

| PDF | Key ideas used |
|---|---|
| **Sellbird Setup Kit** (Hinglish guide to the open-source Sellbird tool) | pipeline find → why they'd buy (signals) → decision-maker → verify email → write & send → handle replies; nothing sent without approval; daily cap, warm-up, office hours, SPF/DKIM/DMARC, separate sending domain; first week plan (look first, then 10–20/day by hand, then autopilot) |
| **4 AI Employees** (Lead Hunter, Auditor, Writer, Closer) | find local businesses with a visible gap, write 2 real problems, one personal message < 80 words, no price in first message, soft yes/no question; Closer answers: how much / portfolio / not now / have someone / how long / interested; follow up day 3 and 7 |
| **The Cold Email Hack Playbook** (Om Kute) | find the trade; credibility (show don't claim), specific ask, transparency, extra effort; formula subject/hook/credibility/opportunity/ask, 70–120 words; follow-ups day 0/3/7/12/18 each adding something new; deliverability; track sent/bounce/reply/positive/meetings; "rules I never break" |
| **The Client Onboarding System** (Poojan Patel) | after "yes": Contract → Payment → Welcome document → Kickoff call → Access → Client portal; "no payment, no onboarding"; remove chaos |

## 3. Timeline of the work (what was asked → what was done)

1. **v1.0:** full pipeline built in an empty repo (which only had a Visual Studio `.gitignore`; Python chosen). Email
   outreach, follow-up tracks, reply classification, onboarding, dashboard, CLI, tests, user guide, playbook, legal doc,
   contract/proposal/welcome templates. Pushed after the owner approved repo access.
2. **Sellbird analysis** (github.com/ethanplusai/sellbird, ~74k lines, AGPL-3.0): strong at finding/researching/first
   emails, but it stops at the first reply: no warm/proposal/nurture tracks, "not now" = closed, no onboarding,
   only 3 emails. **Decision: keep our project, port Sellbird's best ideas rewritten in our own code** (AGPL: no copying).
3. **v1.1 ("one go"):** world rollout (area by area, tiles), all channels (email automatic; WhatsApp/Instagram/
   LinkedIn/X/Facebook/calls as one-click tasks), Sellbird-inspired research (email patterns, more signals, ATS hiring,
   signal filters), Serper + Google Places + Google Sheets import, multiple mailboxes, background service
   (`leadpipe service install`), keep-awake, simpler 5-page UI with a browser setup form, isolated Claude CLI + AI budget.
4. **Settings review:** the owner sells AI automation, but defaults were website-oriented (a café got a "website
   mock-up" WhatsApp message). **Added service types** (website / automation / marketing / seo / reviews / custom)
   that set lead scoring, fix wording and the no-website offer; leads are re-checked and unsent messages rewritten when
   settings change; country field validation ("INDIA" → IN; own start country never skipped); honest copy without proof.
5. **Docs:** Developer Guide written; PDFs of User Guide (22 p) and Developer Guide (23 p) in `docs/pdf/`.

Commits on main: `c58dfe4` v1.0 → `9a0506e` v1.1 → `af52b4e` service types → `7a625c7` developer guide + PDFs.

## 4. Key decisions (and why). Don't undo these without a reason

| Decision | Why |
|---|---|
| Python, SQLite, Flask/Waitress, single process (`leadpipe start`) | simplest to run on a Windows PC; one database file to back up |
| **Only email is automatic**; WhatsApp/Instagram/LinkedIn/X/Facebook/calls are one-click To-do tasks (wa.me with pre-filled text, copy + open profile, tel: + call script + outcome buttons) | platforms ban automated DMs (WhatsApp policy forbids cold API messages); robocalls/AI cold calls are illegal in many places (TCPA, TRAI, PECR). Owner was told this and accepted it |
| Test mode (`sending.mode: dry_run`) by default; approval before sending until autopilot is turned on | safety; matches the playbooks |
| Separate sending domain + Google Workspace recommended; personal @gmail.com warned against | deliverability and account safety |
| World rollout = start area → next areas → home country cities → country list, city by city; big cities in 9 tiles | "start from one place, then the world" |
| Sellbird ideas re-implemented, not copied | AGPL-3.0 license |
| Templates never claim results that aren't in `profile.proof` | honesty rule from the playbooks |
| Service type presets | the product must fit non-website services (owner sells AI automation) |

## 5. Current state (v1.1+)

**Works and tested (42 automated tests, all passing; no network needed):**
- Discovery: areas rollout (`areas` table), OpenStreetMap (Nominatim + Overpass, tiles), Serper, Google Places,
  CSV/Google Sheets import (Maps-scraper columns, `priority` for high-value), recurring sheet sync.
- Research: website audit with 22 signals + evidence, socials/phones, tech stack, agency credit, ATS roles, email
  pattern learning, verification statuses (valid/risky/pattern/guess/invalid), scoring, `rescore_all`.
- Tracks: `cold` (email: day 0/3/5 call/7/10 DM/12/18), `cold_social` (no email: DM/call), `warm`, `proposal`,
  `nurture`, `client_care`; meeting → proposal automatically; close after `close_after_days`.
- Replies: IMAP (read-only, all mailboxes), rule + AI classification, stage transitions, Closer drafts, alerts.
- Onboarding checklist with automatic emails and reminders.
- Sending: multiple mailboxes, warm-up, caps, office hours in recipient timezone, gaps, threading, List-Unsubscribe.
- To-do page (replies, decisions, calls, messages, emails, client steps), call outcomes, logged WhatsApp/DM replies.
- Background service files for Windows/macOS/Linux, keep-awake, file logs, single-instance lock, live config reload.
- Dashboard: Home / To-do / Leads / Clients / Settings (+ Insights), CSRF + optional password, unsubscribe page,
  booking webhook.

**Not verified in a real environment yet (needs the owner's machine/internet):**
- Live OpenStreetMap search (the development sandbox blocked Overpass; Nominatim worked).
- `leadpipe service install` on real Windows (task XML is generated and unit-tested, not run on Windows).
- Real SMTP/IMAP sending and reading (only test mode used so far).
- Claude CLI calls on Windows (the CLI might be a `.cmd` shim; if AI fails, templates are used. Check the log for "AI call failed").

**Owner's current settings (Settings → Setup), as last seen:** service "AI automation for enquiries, bookings and
follow-ups on WhatsApp and email"; starter offer "a WhatsApp auto-reply and booking assistant, live in 5 days"; price
"₹8,000 to ₹15,000, $100 to $180"; quick win "a free 2-minute video showing how your enquiries could be answered
automatically"; booking link cal.com; business types dentist, clinic, salon, spa, gym, real_estate, coaching, hotel,
car_repair; start area Surat, India; world rollout on; countries AE, GB, US, AU, CA, SG, NZ, IE, SA, QA, ZA, MY;
test mode; sending mailbox was a personal Gmail (warned to switch to a Workspace mailbox on a separate domain before
going live). **To do on the owner's side:** pick *Type of service = AI automation* in Setup and save; fix "Never search
in" (had "INDIA"); add IMAP/SMTP App Password when ready; `leadpipe service install`.

## 6. Open items / ideas for next steps

1. Confirm on the owner's PC: live discovery finds Surat businesses; To-do fills within ~1 hour; service install works
   after a reboot; AI writing via `claude` CLI works on Windows (else fix argument passing / use API key).
2. Going live: buy a sending domain (e.g. getindrix.com), Google Workspace, SPF/DKIM/DMARC, `leadpipe doctor`, 2–4 weeks warm-up.
3. Optional improvements discussed or worth considering:
   - per-country price ranges (INR for India, USD/AED elsewhere) instead of one `price_range` string;
   - showing the owner's Google Sheet columns (the sheet could not be read from the sandbox; it must be shared
     "Anyone with the link → Viewer" or downloaded as CSV);
   - a telecaller/team mode (export call list exists: `leadpipe export --calls`);
   - more lead sources (DataForSEO, TinyFish), consent-gated callbacks, calendar auto-booking;
   - cloud deployment (small VPS) for true 24/7 when the PC is off.

## 7. How to work on the code

- Read `docs/DEVELOPER_GUIDE.md` (architecture, every module, data model, config reference, extension recipes).
- Setup: `python -m venv .venv`, activate, `pip install -e ".[dev]"`. Tests: `pytest` (42 pass, ~3–5 s).
  Lint: `python -m pyflakes leadpipe tests scripts`.
- Try things safely: `leadpipe demo` then `leadpipe -c demo/config.yaml dashboard`; `simulate-reply`; `run --once -v`.
- Settings source of truth: `leadpipe/default_config.yaml` (every key commented). Secrets only in `.env`.
- DB changes go through `db.MIGRATIONS` (existing installs upgrade automatically).
- Tests must not use the network or real AI (`llm.provider: none` in test configs; DNS stubbed in `tests/conftest.py`).
- Rebuild PDFs after editing guides: `python scripts/build_docs.py` (see Developer Guide §20).
- Commit to `main` with clear messages; run tests first. Never commit `.env`, `config.yaml` or `data/`.

## 8. Important files

| Path | What |
|---|---|
| `README.md` | overview, quick start |
| `docs/USER_GUIDE.md` (+ PDF) | how to set up and use it, in simple words |
| `docs/DEVELOPER_GUIDE.md` (+ PDF) | how the code works and how to extend it |
| `docs/SALES_PLAYBOOK.md` | sales rules from the 4 PDFs + call scripts, objections, pricing |
| `docs/LEGAL_AND_COMPLIANCE.md` | email/WhatsApp/DM/calling rules per country (not legal advice) |
| `docs/templates/` | service agreement, proposal, welcome document, kickoff agenda, access checklist, client portal |
| `leadpipe/default_config.yaml` | all settings with explanations |
| `leadpipe/engine.py`, `leadpipe/service.py` | the 24/7 loop and the background service |
| `leadpipe/dashboard/` | the web UI |
| `tests/` | the test suite |
