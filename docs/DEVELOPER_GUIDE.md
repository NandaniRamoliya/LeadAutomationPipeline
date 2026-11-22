# Developer Guide: Orbit

For anyone who wants to understand, change, extend or deploy the code. For *using* the product, see the
[User Guide](USER_GUIDE.md).

**Contents**

1. [Overview and design principles](#1-overview-and-design-principles)
2. [Development setup](#2-development-setup)
3. [Repository layout](#3-repository-layout)
4. [How it runs](#4-how-it-runs)
5. [Data model](#5-data-model)
6. [The lead lifecycle](#6-the-lead-lifecycle)
7. [Discovery: finding businesses](#7-discovery-finding-businesses)
8. [Research, scoring and qualification](#8-research-scoring-and-qualification)
9. [Channels and the To-do tasks](#9-channels-and-the-to-do-tasks)
10. [Writing messages](#10-writing-messages)
11. [Sending email](#11-sending-email)
12. [Reading and handling replies](#12-reading-and-handling-replies)
13. [Client onboarding](#13-client-onboarding)
14. [The AI layer](#14-the-ai-layer)
15. [The dashboard](#15-the-dashboard)
16. [Configuration reference](#16-configuration-reference)
17. [Command-line interface](#17-command-line-interface)
18. [Extending: recipes](#18-extending-recipes)
19. [Testing](#19-testing)
20. [Deployment and operations](#20-deployment-and-operations)
21. [Conventions and contributing](#21-conventions-and-contributing)

---

## 1. Overview and design principles

**Orbit** (by Indrix; GitHub repository `LeadAutomationPipeline`) is a single Python package (`leadpipe`, commands `leadpipe` and `orbit`) that runs an outbound sales pipeline 24/7:
**find → research → score → write → send / task → read replies → move to the right follow-up track → onboard**.

| Principle | What it means in the code |
|---|---|
| **Deterministic where possible** | Website research, scoring, channel choice, scheduling, safety checks and the engine's priorities are plain code. AI is only used to *write* and to *understand* replies, and every AI output is re-checked. |
| **Never stop the loop** | Every engine job is wrapped; a failure is logged to `events` and retried next cycle. Missing network, AI or mailbox degrade gracefully. |
| **Nothing unsafe by default** | Test mode (`dry_run`), approval before sending, caps and warm-up, suppression checked at send time, one engine per database. |
| **Honest messages** | Templates and the AI only use facts recorded with evidence; no invented results, compliments or numbers. |
| **One file of state** | Everything lives in one SQLite database (`data/leadpipe.db`). Back it up and you have everything. |
| **Humans for risky channels** | Only email is sent automatically. WhatsApp, Instagram, LinkedIn, X, Facebook and calls are prepared as one-click tasks. |

Stack: Python ≥ 3.10, SQLite (WAL), Flask + Waitress, `requests`, BeautifulSoup, `dnspython`, `phonenumbers`,
`ruamel.yaml`, `PyYAML`, `python-dotenv`, `tzdata`, optional `anthropic`. Tests: `pytest`.

---

## 2. Development setup

```bash
git clone https://github.com/NandaniRamoliya/LeadAutomationPipeline.git
cd LeadAutomationPipeline
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                             # 42 tests, no network needed, ~3 s
```

Useful during development:

```bash
leadpipe demo --folder demo                   # a throw-away database with every situation
leadpipe -c demo/config.yaml dashboard        # UI on http://localhost:8787
leadpipe -c demo/config.yaml run --once -v    # one engine cycle, verbose
leadpipe -c demo/config.yaml simulate-reply 4 "How much would this cost?"
python -m pyflakes leadpipe tests             # lint (keep it clean)
```

`-c path/to/config.yaml` selects a setup; its folder holds `.env` and `data/` (database, logs, dry-run emails).
`LEADPIPE_DATA_DIR` and `LEADPIPE_DB` override the data location (the tests use them).

---

## 3. Repository layout

```
leadpipe/
  __main__.py, cli.py     `leadpipe` commands (argparse)
  config.py               Config object, load/merge defaults, live reload, comment-preserving writer
  default_config.yaml     every setting with its default and an explanation (the source of truth)
  db.py                   SQLite schema, migrations, small helper API, kv store, event log
  engine.py               the 24/7 loop, job scheduling, single-instance lock, engine status
  service.py              background service files (Windows/macOS/Linux), keep-awake, file logging
  pipeline.py             stages, follow-up tracks, set_stage / advance / skip / timers, suppression
  discovery/              areas rollout, OpenStreetMap, Serper, Google Places, CSV / Google Sheets import
  enrich/                 website audit, email patterns, email verification, scoring, re-scoring
  channels.py             phones, social handles, channel availability and choice, one-click links
  presets.py              service types (website / automation / marketing / seo / reviews)
  templates.py            signals catalogue, email/DM templates, call scripts, Closer replies, onboarding emails
  writer.py               context building, AI/template drafting, queueing, write_due, rewrite_waiting
  compliance.py           pre-send checks and the legal footer
  mailboxes.py            sending mailboxes (main from .env + extras from config)
  sender.py               the send job: caps, warm-up, office hours, gaps, threading, dry-run
  inbox.py                IMAP reader (read-only) and message parsing
  classifier.py           reply intent: rules + optional AI
  handler.py              reply -> match lead -> intent -> stage -> drafted answer -> alert
  tasks.py                To-do list, call outcomes, DM completion, logged replies, call-list export
  onboarding.py           contract -> payment -> welcome -> kickoff -> access -> portal -> client care
  analytics.py            overview numbers, per-step / per-signal / per-category rates, daily digest
  notify.py               alerts: email, webhook (Slack/Discord), Telegram
  llm.py                  Claude API / Claude CLI / none, JSON-schema calls, daily budget
  doctor.py               setup checks (DNS, SMTP, IMAP, AI, service), `train` profile helper
  geo.py                  offline timezones (tzdata zone table), world city list, country names
  demo.py                 the demo database
  dashboard/app.py        Flask routes; templates/ (Jinja2) and static/style.css
tests/                    pytest suite (fixtures/site/ is a tiny website for audit tests)
docs/                     user guide, developer guide, playbook, legal, templates, PDFs
deploy/                   systemd unit, Windows .bat, Caddy example
Dockerfile, docker-compose.yml, pyproject.toml, requirements.txt, .env.example
```

---

## 4. How it runs

### Processes and threads

`leadpipe start` runs one process: the **engine** in a background thread and the **dashboard** (Waitress) in the
main thread. Both share one `Config` object and one `DB` object (thread-safe through an `RLock`; SQLite WAL also
allows a separate `leadpipe dashboard` process). `leadpipe run` is the engine alone; `leadpipe dashboard` is the UI alone.

**Single instance:** `data/engine.lock` holds the engine's PID. `engine_running_elsewhere()` checks whether that PID
is alive (`os.kill(pid, 0)`, or `tasklist` on Windows). A second `start` exits with "already running"; this is what
makes the Windows 5-minute "start if not running" trigger safe.

### The engine cycle (`engine.Engine.cycle`)

Every `engine.tick_seconds` (default 60):

| Order | Job | Frequency | Function |
|---|---|---|---|
| 0 | heartbeat, reload settings if `config.yaml`/`.env` changed, stop if `engine_paused` | every tick | `Config.reload_if_changed` |
| 1 | read replies from every mailbox | `inbox_every_minutes` (5) | `handler.process_inbox` |
| 2 | close finished tracks, move booked calls to proposal | every tick | `pipeline.process_timers` |
| 3 | advance onboarding | every tick | `onboarding.tick` |
| 4 | send ≤ 1 email per mailbox | every tick (gap-limited) | `sender.send_due` |
| 5 | enroll new leads, write due messages/tasks | `write_every_minutes` (10) | `pipeline.enroll_new_contacts`, `writer.write_due` |
| 6 | research new companies | `enrich_every_minutes` (15) | `enrich.enrich_pending` |
| 7 | search the next map tile when the queue is low | `discover_every_minutes` (30) and `fresh_leads < min_ready_leads` | `discovery.run_discovery` |
| 8 | re-import Google Sheets | `sources.sheet_sync_hours` (6) | `csv_import.sync_sheets` |
| 9 | daily digest | once a day after `daily_digest_hour` | `analytics.digest_text` + `notify` |

Job timestamps are stored in `kv` (`job:<name>`), so restarts don't reset schedules. Each job runs through
`Engine._run`, which catches every exception and logs it.

### Background service and keep-awake (`service.py`)

`leadpipe service install` writes and registers an OS definition that runs
`<python> -m leadpipe -c <config> start --background`:

| OS | Mechanism | Key properties |
|---|---|---|
| Windows | Task Scheduler XML via `schtasks /Create /XML` | Log-on trigger + time trigger every 5 min, `MultipleInstancesPolicy=IgnoreNew`, `WakeToRun`, no execution time limit, runs on battery, restart on failure, `pythonw.exe` (no window) |
| macOS | `~/Library/LaunchAgents/com.leadautomationpipeline.engine.plist` | `RunAtLoad`, `KeepAlive` |
| Linux | `~/.config/systemd/user/leadpipe.service` | `Restart=always`; `loginctl enable-linger` to survive logout |

`--background` logs to `data/logs/leadpipe.log` (rotating, 5 × 5 MB) and prints nothing; `cli.main` replaces a
missing `stdout`/`stderr` (pythonw) with `os.devnull`. While the engine runs, `keep_awake()` blocks system sleep
(`SetThreadExecutionState` on Windows, `caffeinate -w <pid>` on macOS, `systemd-inhibit` on Linux).

### Live settings

The dashboard writes `config.yaml` with `config.save_settings()` (ruamel round-trip, comments kept) and `.env` with
`config.save_env()`. `Config.reload_if_changed()` compares file mtimes and replaces `cfg.data` **in place**, so every
module holding the same `Config` object sees the new values on the next cycle.

---

## 5. Data model

All tables are created in `db.SCHEMA`; columns added later are listed in `db.MIGRATIONS` and applied with
`ALTER TABLE … ADD COLUMN` on every start, so old databases upgrade automatically.

| Table | Purpose | Important columns |
|---|---|---|
| `areas` | the rollout queue | `name`, `country`, `timezone`, `bbox` (JSON s,w,n,e), `position`, `status` (pending/active/done/skipped/failed), `tile`, `tiles`, `found` |
| `companies` | one business | `website`, `domain`, `phone`, `whatsapp`, `instagram`, `linkedin`, `twitter`, `facebook`, `listed_email`, `rating`, `reviews`, `priority`, `signals` (JSON with evidence), `problems`, `score`, `status` (new/qualified/disqualified/error), `status_reason`, `area_id`, `timezone`, `tech`, `source`, `source_ref` |
| `contacts` | a person (email) or a `social` contact (no email) | `email`, `email_status`, `channel`, **`stage`**, **`sequence`**, **`step`**, `next_action_at`, `mailbox`, `phone`, `call_attempts`, `lost_reason` |
| `messages` | every outbound draft/task and inbound reply | `direction` (out/in), `channel`, `kind` (sequence/reply/onboarding/inbound), `status` (pending/approved/sent/failed/cancelled/rejected/received), `purpose`, `subject`, `body`, `check_issues` (JSON), `action_url`, `outcome`, `message_id`, `in_reply_to`, `mailbox`, `intent`, `needs_human`, `scheduled_for`, `sent_at` |
| `deals` | won deals | `status` (onboarding/active), `value`, `currency`, `checklist` (JSON key→timestamp), `reminders` |
| `suppression` | do-not-contact | `value`: email, `@domain`, phone digits/E.164, profile URL, or `sha256:<hash>` (erased person) |
| `events` | audit log shown in the UI | `ts`, `level`, `kind`, `message`, `contact_id`, `company_id` |
| `seen_inbound` | inbound Message-IDs that matched no lead | |
| `kv` | runtime state (JSON values) | see below |

Notable `kv` keys: `heartbeat`, `engine_paused`, `sending_paused`, `autopilot_cold`, `autopilot_replies`,
`job:<name>`, `first_send_date[:<mailbox>]`, `next_send_at[:<mailbox>]`, `enrolled:<date>`, `llm_calls:<date>`,
`last_discovery_at`, `last_inbox_at`, `digest:<date>`, `secret`, `flask_secret`.

`DB` helpers: `all`, `one`, `scalar`, `insert(table, dict)`, `update(table, id, dict)` (sets `updated_at`),
`kv_get/kv_set`, `log(kind, message, level, contact_id, company_id)`. Times are ISO-8601 UTC strings
(`db.now_iso()`, `iso()`, `parse_iso()`).

---

## 6. The lead lifecycle

### Stages and tracks (`pipeline.py`)

```
new ──enroll──▶ cold (email) / cold_social (no email)
                 │
                 ├─ interested ──▶ warm track ──booked──▶ meeting ──(next day)──▶ proposal track
                 │                                                                   │
                 │                                                     YES ──▶ won (onboarding) ──▶ client (client_care)
                 ├─ not now / have someone ──▶ nurture (30/60/90 days) ──▶ lost
                 ├─ no ──▶ lost          ├─ stop ──▶ unsubscribed          ├─ bounce ──▶ bounced
                 └─ no reply after the whole track ──▶ lost
```

`STAGE_SEQUENCE` maps a stage to its track; `sequence_for(stage, contact)` sends no-email leads to `cold_social`.
Tracks are defined in `config.yaml → sequences` as steps `{day, purpose, channel}` plus `on_complete`
(`lost`/`nurture`/`none`) and `close_after_days`.

| Function | Does |
|---|---|
| `set_stage(db, cfg, contact_id, stage, reason, start_at=None, first_delay_days=None)` | cancels queued sequence messages (all queued messages for terminal stages), sets the new track at step 0 with `next_action_at = start + steps[0].day`, suppresses on unsubscribe/bounce, logs |
| `advance_after_send(db, cfg, message)` | after a step was sent/done: `step += 1`, next date = now + (day gap to the next step); after the last step, now + `close_after_days` |
| `skip_step(db, cfg, contact, reason)` | move past a step that can't happen (no channel, score too low), keeping the spacing |
| `process_timers(db, cfg)` | finished tracks → `on_complete`; `meeting` → `proposal` after the call date |
| `enroll_new_contacts(db, cfg)` | qualified `new` contacts → `cold`, up to `sending.new_leads_per_day`, high-value and high-score first |
| `pause_colleagues`, `delay_next_action`, `suppress`, `is_suppressed`, `cancel_pending` | helpers |

---

## 7. Discovery: finding businesses

### Areas (`discovery/__init__.py`)

- `seed_areas(db, cfg)` builds the queue (idempotent): `reach.start_area`, `reach.next_areas`, then, if
  `reach.world_rollout`, the home country's cities from `geo.WORLD_CITIES`, then each country in `reach.countries`.
  Countries in `reach.skip_countries` or `compliance.restricted_countries` are inserted as `skipped`.
- `current_area(db)` returns the `active` area or activates the next `pending` one.
- `run_discovery(db, cfg, area_name=None, steps=1)`: geocodes the area once (`osm.locate_area` → bbox, country;
  `geo.timezone_for` → timezone), splits the bbox into an n×n grid (`tile_bbox`, n² = `reach.tiles_per_area`) and
  searches **one tile per step**. OSM searches the tile; Serper/Google search the whole area on tile 0. When the last
  tile is done the area becomes `done` and the next one is activated on the next call.
- `discovery_due()` = `reach.auto_discover` and the time gap and `fresh_leads() < reach.min_ready_leads`.
- `save_company(db, data)` dedupes by (`source`, `source_ref`), website domain, or name+city, and merges new details
  (phones, socials, ratings, priority) into an existing row. Social-media "websites" are moved into the social fields.

### Sources

| Module | Function | Notes |
|---|---|---|
| `osm.py` | `locate_area`, `build_bbox_query`, `search_tile`, `parse_elements`, `tags_for` | Nominatim (1 req/s, identifying User-Agent) + Overpass with a fallback server; `CATEGORY_TAGS` maps plain words to OSM tags, raw `key=value` accepted |
| `serper.py` | `discover_serper` | `SERPER_API_KEY`; Google Maps places with rating/reviews |
| `google_places.py` | `discover_google` | `GOOGLE_PLACES_API_KEY`; Places Text Search (New) with field mask |
| `csv_import.py` | `import_csv`, `map_headers`, `sheet_csv_url`, `fetch_sheet`, `sync_sheets` | loose header matching incl. Maps-scraper names (`title`, `totalScore`, `reviewsCount`, `categoryName`, `url`); Google Sheets link → CSV export; phone normalisation; `priority` |

Each source returns plain dicts with: `name, category, website, phone, email, address, city, country, instagram,
facebook, linkedin, twitter, whatsapp, rating, reviews, lat, lon, maps_url, source, source_ref`.

---

## 8. Research, scoring and qualification

### Website audit (`enrich/website.py`)

`audit_website(url, timeout, max_pages)` → `SiteAudit(signals, emails, people, socials, phones, tech, summary, …)`.
It fetches the homepage, then up to `max_pages-1` internal pages ranked by `SUBPAGE_RANK` (contact, team, about…,
careers, blog). Charset is detected when the server doesn't declare one.

| Signal key | Detection |
|---|---|
| `no_website`, `instagram_only` | no URL / URL is a social or directory page |
| `site_broken`, `no_https`, `slow_site` | request error or HTTP ≥ 400; final URL is http; > 3 s |
| `not_mobile_friendly`, `outdated_site`, `weak_seo` | no viewport meta; latest © year < now−2; missing title/description |
| `no_online_booking`, `no_contact_form`, `no_live_chat`, `no_analytics`, `running_ads` | keyword/script lists (`BOOKING_HINTS`, form inputs, `CHAT_HINTS`, `ANALYTICS_HINTS`, `ADS_HINTS`) |
| `diy_builder`, `agency_built`, `no_schema_markup`, `blog_stale` | `TECH_HINTS`; footer credit ("Website by X", `find_agency`); no JSON-LD/itemtype; newest date on a blog page |
| `hiring` | careers hints, or open roles from Greenhouse/Lever/Ashby/Workable public feeds (`ats_roles`) |
| `startup_backed`, `recently_funded` | the company's own words (`STARTUP_RE`, `FUNDING_RE`) |
| `low_rating`, `few_reviews` | from listing data (`enrich.reputation_signals`): < 4.0★, < 15 reviews |

Also extracted: emails (mailto, Cloudflare `data-cfemail`, `[at]`/`[dot]`), people (name–role patterns, JSON-LD
`founder`/`employee`, "Dr." names), social links (`channels.extract_socials`), `tel:` numbers, tech stack.

### Emails (`enrich/patterns.py`, `enrich/verify.py`)

- `learn_pattern(emails, people, domain)` finds the format that explains a published address of a named person
  (`first.last`, `flast`, `firstl`…); `apply_pattern` builds the owner's address and returns `None` if another
  named person would get the same one.
- `verify_email(email, cfg, guessed=False, pattern=False)` → `valid` / `risky` (role inbox) / `pattern` / `guess` /
  `invalid`, via syntax, disposable/placeholder lists, MX (or A) records, optional Hunter API and optional SMTP probe.
  `is_sendable(status, cfg)` applies `allow_role_emails`, `allow_pattern_emails`, `allow_guessed_emails`.

### `enrich_company(db, cfg, company, llm=None, audit=None)`

1. audit (or use the given `audit`, which is how tests inject data); add reputation signals; filter `hiring` by `enrichment.hiring_roles`
2. fill missing phone/socials/tech on the company
3. contacts (max `max_contacts_per_company`): owner's published address → owner's pattern address → other published addresses → optional blind guess
4. no usable email but a phone/profile → one `social` contact
5. `score_company`: Σ signal weights + 10 named person + 5 valid email + `priority_bonus`
6. optional AI summary of the two main problems
7. qualify: restricted country / unreachable / score < `min_score` (unless priority) → `disqualified` (contacts → `lost`)

`rescore_all(db, cfg)` re-applies scoring after settings change: re-qualifies leads, sets aside untouched leads that no
longer fit (or whose OSM category is no longer in `reach.business_types`) and cancels their waiting messages.

---

## 9. Channels and the To-do tasks

### `channels.py`

| Function | Does |
|---|---|
| `parse_phone(raw, country)` | `phonenumbers` → `{e164, display, mobile, country}` |
| `extract_socials(urls)` | canonical Instagram/Facebook/LinkedIn/X URLs and WhatsApp numbers; share/intent links ignored |
| `available(company, contact, cfg)` | `{channel: target}` for enabled channels; mobile numbers (or a WhatsApp link) enable WhatsApp |
| `pick_channel(step_channel, avail, cfg)` | `email` → falls back to a DM; `dm`/`auto` → first of `channels.dm_order`, then `call`; a specific app → that app or a DM fallback |
| `action_url(channel, target, text)` | `wa.me/<n>?text=…`, `ig.me/m/<h>`, `m.me/<h>`, `tel:`, `mailto:`, profile URL |

### `writer.write_due` channel rules

For each due step: resolve the channel; if none → `skip_step`. For non-email channels on leads that *have* email
(extra touches): require score ≥ `channels.min_score_for_tasks` or `priority`, else skip. Respect
`channels.tasks_per_day` (excess is postponed ~20 h). Non-email messages are stored with `channel`, `to_addr`
(target) and `action_url`; **they are never sent by code**.

### `tasks.py`

| Function | Does |
|---|---|
| `todo(db, cfg)` | sections `replies`, `decide`, `calls`, `messages`, `emails`, `clients`, ordered by priority/score |
| `done(db, cfg, message_id, body=None)` | DM sent by the user → `sent` + `advance_after_send` |
| `skip(db, cfg, message_id)` | cancel and advance |
| `call_outcome(db, cfg, message_id, outcome, notes)` | `no_answer` (retry next day until `call_attempts`), `voicemail`, `wrong_number` (removes phone), or a real answer → `handler.apply_intent` with the matching intent |
| `log_reply(db, cfg, contact_id, text, intent=None, channel=…)` | a reply received outside email, handled like an email reply, answer drafted on the same channel |
| `export_call_list(db, cfg)` | calls due today as rows (for `leadpipe export --calls`) |

---

## 10. Writing messages

### Context (`writer.build_context`)

Turns company + contact + profile into template variables: `greeting`, `first_or_team`, `company`, `website`,
`category_plural`, `service`, `starter_offer`, `price_range`, `quick_win` (adapted for no-website leads),
`proof_line` (empty without proof: **nothing is claimed**), and from the top two signals (by weight):
`obs1/obs2` (observation), `cost1/cost2`, `fix1/fix2`, `problem1`, `topic1`, plus call-script fields
(`phone_line`, `online_presence`, `business_name`). Service-type wording overrides come from `presets.override()`.

### Templates (`templates.py`)

| Object | Content |
|---|---|
| `SIGNALS` | per signal: `label`, `explain`, `problem`, `observation`, `cost`, `fix`, `topic` |
| `SEQUENCE_TEMPLATES` | per purpose: list of `(subject or None, body)` variants; `None` = reply in the same thread |
| `CALL_TEMPLATES` | `call_intro`, `call_followup` scripts |
| `REPLY_TEMPLATES` | Closer answers per intent |
| `ONBOARDING_TEMPLATES` | contract, invoice, welcome, access, portal, reminders |

`pick(variants, seed)` chooses a variant deterministically per contact (a new seed on "rewrite"); `fill()` never leaves
placeholders (missing keys render empty).

### Service types (`presets.py`)

`PRESETS[type]` = `weights` (signal weights), `fixes`/`costs` (wording overrides), `quick_win_no_site`. When
`profile.service_type` is not `website`/`custom` and the user config has no `signals:`, `load_config` applies the
preset's weights. The dashboard writes the weights into `config.yaml` when the type changes.

### Drafting (`Writer`)

`sequence_draft(contact, company, purpose, extra, seed, channel)`: call → `CALL_TEMPLATES`; DM → maps email purposes to
DM purposes (`DM_EQUIVALENT`); otherwise AI first (`_ai_draft`: facts + purpose brief + thread history + word limit,
JSON schema `{subject, body}`), re-checked by `compliance.check_message` and retried once with the failures; falls back
to the template. `reply_draft` (Closer) and `onboarding_draft` work the same way. `queue_message(...)` stores the draft
with its check results, channel, target and action URL; `auto_ok` approves passing emails (autopilot).
`regenerate()` rewrites one draft; `rewrite_waiting()` rewrites all unsent outreach.

### Safety checks (`compliance.check_message`)

Returns issues `{level: block|warn, rule, detail}`: unfilled placeholders, banned phrases (spam/AI clichés + your own),
length per purpose (`WORD_LIMITS`), links (0 in a first message), price in a first message, misleading `Re:`/`Fwd:`,
shouting, missing company name or question. Call scripts are only checked for placeholders. `compliance.footer()` adds
signature, postal address, opt-out line and unsubscribe URL **at send time**.

---

## 11. Sending email

`mailboxes.mailboxes(cfg)` returns the main mailbox (from `.env`) plus `config.yaml → mailboxes` (password from
`password_env`). `sender.send_due(db, cfg, mailer, now, respect_timing=True, max_send=1)`:

1. stop if `sending_paused`
2. order mailboxes by remaining capacity; for each: skip if inside its random gap
3. per approved, due email (priority: replies > onboarding > follow-ups > first emails):
   hard checks (`_hard_check`: address present & sendable, not suppressed, contact not closed, still on this step;
   a courtesy reply to a "no" is allowed) → mailbox ownership (a lead stays with its mailbox) → new-lead quota →
   recipient office hours → one company per day → re-run compliance → build → send
4. on success: store `message_id`, `mailbox`, pin the contact to the mailbox, set warm-up start, advance the track,
   set the next random gap; on `SMTPRecipientsRefused` → bounced; on other errors → `failed` + 15-minute back-off

`todays_cap(db, cfg, now, mailbox)` = min(cap, `start_per_day + increase_per_day × days since first send`).
`build_email()` sets From/To/Subject/Date/Message-ID, threads follow-ups (`In-Reply-To`, `References`) and adds
`List-Unsubscribe` (mailto, plus one-click URL when `dashboard.public_base_url` is set). `Mailer.send()` writes `.eml`
files in `dry_run` mode, else SMTP (`starttls`/`ssl`/none).

---

## 12. Reading and handling replies

- `inbox.fetch_new()` reads every mailbox with IMAP settings, folders from `IMAP_FOLDERS`, last 4 days, **read-only**
  (`BODY.PEEK`), skipping Message-IDs already stored. `parse_message()` extracts plain text (HTML fallback), headers
  needed for auto-reply detection, and DSN failed recipients.
- `handler.match_contact()`: `In-Reply-To`/`References` → DSN recipients → sender address → same company domain
  (creates a colleague contact). Unmatched mail is marked seen and ignored.
- `classifier.classify()`: deterministic rules first for **bounce, auto-reply, unsubscribe** (safety-critical), then
  AI (confidence ≥ 0.5) or keyword rules. Quoted history is stripped. `followup_days()` reads "in 2 months" etc.
- `handler.apply_intent(db, cfg, contact, mail, result, writer, channel_hint=None)` stores the inbound message, stops
  the lead's sequence (and colleagues' cold outreach), changes stage, drafts the answer (email, or the DM channel the lead
  uses), creates referral contacts, and notifies for hot intents.

| Intent | Transition |
|---|---|
| `interested`, `price_question`, `portfolio_request`, `timeline_question`, `question`, `referral` | → `interested` (warm track) |
| `meeting_booked` | → `meeting` |
| `proposal_request`, `ready_to_buy` | → `proposal` (ready_to_buy: chasers stopped, needs the user to mark WON) |
| `not_now` / `already_have` | → `nurture` (first check-in after their delay / 90 days) |
| `not_interested` | → `lost` + suppress + optional courtesy reply |
| `unsubscribe` / `bounce` / `auto_reply` | → `unsubscribed` / `bounced` / push next step by `ooo_delay_days` |

---

## 13. Client onboarding

`onboarding.start(db, cfg, contact_id, value, currency)` creates the deal and moves the contact to `won`
(colleagues are paused). `CHECKLIST` = `(key, label, who)`; `who="auto"` items tick themselves when the matching email
is sent (`SENT_FLAG`), `who="you"` items are ticked in the UI (`set_step`). `tick()` drafts the next email in order
(contract → invoice → welcome → access request → portal) and reminders every `reminder_every_days` (max
`max_reminders`); when the portal email is sent, `deal_done()` moves the contact to `client` (client-care track).

---

## 14. The AI layer

`llm.LLM(cfg)` picks a provider: `anthropic` (API key), `claude_cli` (the `claude` command, uses the user's Claude
plan) or `none`; `auto` prefers the API key, then the CLI. `LLM.json(system, prompt, schema, effort)` returns a dict or
`None` (never raises).

- **API:** `client.beta.messages.create(model=llm.model, output_config={effort, format: json_schema}, betas=[server-side
  fallback], fallbacks="default")`, falling back to the standard endpoint on a 400; checks `stop_reason` for refusal and
  truncation. Default model `claude-opus-5-5`.
- **CLI:** `claude -p --output-format text` with every isolation flag the installed version supports
  (`--safe-mode`, `--tools ""`, `--setting-sources ""`, `--strict-mcp-config`, `--no-session-persistence`,
  `--disable-slash-commands`), run in an empty temporary directory with the prompt on stdin; JSON is extracted from the output.
- **Budget:** `attach_db(db)` enables `llm.max_calls_per_day` (counter in `kv`).

---

## 15. The dashboard

`dashboard/app.py → create_app(cfg, db, engine=None)` (Flask). Pages: `/` Home, `/todo`, `/leads` (+ `/leads/researching`),
`/lead/<id>`, `/company/<id>`, `/clients`, `/insights`, `/settings?tab=setup|reach|automation|import|dnc`.
Actions are `POST`s: `/todo/<id>/<send|save|done|rewrite|skip|discard|stop|call>`, `/todo/approve-all`,
`/todo/client/<id>`, `/lead/<id>/<action>`, `/settings/setup`, `/settings/toggle/<key>`, `/settings/area…`,
`/settings/import`, `/settings/suppress`, `/engine/<pause|resume|install|uninstall>`, `/run/<job>`.

**Security:** `before_request` enforces HTTP Basic auth when `DASHBOARD_PASSWORD` is set (required for non-localhost
hosts, `serve()` refuses otherwise) and a per-session CSRF token on every POST. Public endpoints: `/health`,
`/u/<token>` (HMAC-signed unsubscribe, RFC 8058 one-click POST) and `/webhooks/booking?token=` (Cal.com/Calendly:
any attendee email → `meeting`).

**Templates** (`dashboard/templates/`): `base.html` (nav, status, copy-to-clipboard script), one file per page.
Context helpers: `status`, `todo_count`, `PLAIN_STAGE`, `LABELS`, `ICONS`, filters `ago`, `short`, `evidence`.
**Adding a page:** add a route in `create_app`, a template extending `base.html`, and a nav link.

---

## 16. Configuration reference

`default_config.yaml` documents every key; the user's `config.yaml` is deep-merged over it (lists replace).
`_upgrade_old_keys` converts 1.0 settings (`targets`, `discovery`).

| Section | Key settings |
|---|---|
| `profile` | `your_name`, `business_name`, `website`, `service_type`, `service`, `service_long`, `starter_offer`, `price_range`, `delivery_time`, `proof`, `proof_by_category`, `proof_links`, `quick_win`, `quick_win_no_site`, `booking_link`, `signature`, `physical_address` |
| `reach` | `auto_discover`, `business_types`, `start_area`, `next_areas`, `world_rollout`, `countries`, `skip_countries`, `tiles_per_area`, `results_per_search`, `min_ready_leads` |
| `sources` | `providers` (osm/serper/google_places), `google_sheets`, `sheet_sync_hours` |
| `enrichment` | `per_cycle`, `request_timeout`, `max_pages_per_site`, `max_contacts_per_company`, `allow_role_emails`, `allow_pattern_emails`, `allow_guessed_emails`, `smtp_probe`, `min_score`, `priority_bonus`, `hiring_roles` |
| `signals` | weight per signal key |
| `channels` | `enabled`, `dm_order`, `tasks_per_day`, `min_score_for_tasks`, `call_attempts` |
| `sending` | `mode` (dry_run/smtp), `daily_cap`, `warmup.{enabled,start_date,start_per_day,increase_per_day}`, `new_leads_per_day`, `office_hours.{start,end,days}`, `min_gap_minutes`, `max_gap_minutes`, `one_company_per_day` |
| `mailboxes` | extra mailboxes `{name, email, from_name, smtp_*, imap_*, password_env, daily_cap, warmup_start_date}` |
| `approval` | `autopilot_cold`, `autopilot_replies`, `draft_ahead_hours` |
| `sequences` | tracks: `cold`, `cold_social`, `warm`, `proposal`, `nurture`, `client_care` → `steps[{day, purpose, channel}]`, `on_complete`, `close_after_days` |
| `onboarding` | `contract_link`, `payment_link`, `welcome_doc_link`, `kickoff_link`, `portal_link`, `reminder_every_days`, `max_reminders`, `access_items` |
| `replies`, `notifications` | `ooo_delay_days`, `reply_to_not_interested`, `stop_company_on_reply`; `owner_email`, `daily_digest_hour` |
| `llm` | `provider`, `model`, `cli_model`, `effort_writing`, `effort_classify`, `timeout_seconds`, `max_calls_per_day` |
| `compliance` | `include_address`, `opt_out_line`, `restricted_countries`, `max_links_first_email`, `max_links_followup`, `extra_banned_phrases` |
| `engine`, `dashboard` | `timezone`, `tick_seconds`, `*_every_minutes`, `keep_awake`; `host`, `port`, `public_base_url` |

**Environment (`.env`):** `FROM_EMAIL`, `FROM_NAME`, `REPLY_TO`, `SMTP_HOST/PORT/SECURITY/USER/PASSWORD`,
`IMAP_HOST/PORT/USER/PASSWORD/FOLDERS`, `ANTHROPIC_API_KEY`, `SERPER_API_KEY`, `GOOGLE_PLACES_API_KEY`,
`HUNTER_API_KEY`, `NOTIFY_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DASHBOARD_PASSWORD`,
`LEADPIPE_SECRET`, `BOOKING_WEBHOOK_TOKEN`, extra mailbox passwords; plus `LEADPIPE_CONFIG`, `LEADPIPE_DATA_DIR`, `LEADPIPE_DB`.

---

## 17. Command-line interface

`cli.build_parser()` defines every command; each maps to a `cmd_*` function that loads the config and database
(`_ctx(args)`). Groups: setup (`init`, `doctor`, `train`, `demo`, `signals`, `categories`), running (`start`, `run`,
`dashboard`, `service`, `open`), pipeline steps (`discover`, `areas`, `import`, `enrich`, `write`, `send`, `inbox`,
`outbox`), leads (`leads`, `contacts`, `stage`, `won`, `onboard`, `stats`, `export`), switches (`sending`, `autopilot`),
privacy (`suppress`, `forget`) and testing (`simulate-reply`). Add a command by writing `cmd_x(args)` and registering
a sub-parser with `set_defaults(fn=cmd_x)`.

---

## 18. Extending: recipes

| Goal | Steps |
|---|---|
| **New lead source** | add `discovery/<name>.py` returning the source dicts (§7); call it in `run_discovery` for its provider name; document the key in `.env.example` and `default_config.yaml` |
| **New signal** | detect it in `enrich/website.py` (put evidence in `audit.signals[key]`); add copy to `templates.SIGNALS` and a phrase to `TOPICS`; add a default weight in `default_config.yaml` and in relevant `presets.PRESETS` |
| **New follow-up step type** | add variants to `SEQUENCE_TEMPLATES` (or `CALL_TEMPLATES`), a brief to `writer.PURPOSE_BRIEFS`, a limit to `compliance.WORD_LIMITS`; use it in `sequences:` |
| **New reply intent** | add to `classifier.INTENTS` and its rules (and the AI schema enum), an answer to `REPLY_TEMPLATES`, a transition in `handler.apply_intent` |
| **New channel** | extend `channels.ALL_CHANNELS`/`LABELS`/`ICONS`, `available()`, `action_url()`, `pick_channel()`; it then appears in tasks automatically |
| **New service type** | add an entry to `presets.PRESETS` (label, weights, fixes, costs, quick_win_no_site) |
| **New dashboard page** | route in `create_app`, template extending `base.html`, nav link |
| **New engine job** | a function `(db, cfg, …) -> int`; schedule it in `Engine.cycle` with `self._due(name, minutes)` / `self._run` / `self._mark` |

---

## 19. Testing

```bash
pytest                         # everything
pytest tests/test_v11_features.py -k rollout -q
pytest --durations=5           # find slow tests
```

| File | Covers |
|---|---|
| `test_rules.py` | compliance checks, footer, classifier cases, quoted text, DSN bounce parsing, verification |
| `test_audit.py` | website audit against a local HTTP server (`fixtures/site/`), Cloudflare decoding, enrichment ranking |
| `test_end_to_end.py` | full journey to client, no-reply → lost, not-now → nurture, unsubscribe, courtesy reply, sender guards |
| `test_dashboard.py` | every page renders, CSRF, password, unsubscribe link, booking webhook |
| `test_v11_features.py` | channels, world rollout (mocked map), Maps import → WhatsApp task, patterns, multiple mailboxes, call outcomes, task quotas, service files, settings writer, old-config upgrade, setup form, service types, honest no-proof copy |

**Fixtures (`tests/conftest.py`):** `cfg` writes a test `config.yaml` (AI off, test mode), sets `LEADPIPE_DATA_DIR` to a
temp folder, clears secrets from the environment and **stubs DNS** (`verify.mx_hosts`) so no network is used; `db`
opens a fresh database. Inject research results with `enrich_company(..., audit=SiteAudit(...))`; simulate replies
with `handler.simulate_reply`; move time with the `time_travel()` helper in `test_end_to_end.py`; send with
`send_due(..., respect_timing=False)` (dry-run `.eml` files land in the temp data folder). Tests must never call the
network or the real AI: keep `llm.provider: none` in any config a test writes.

CI: `.github/workflows/tests.yml` runs the suite on Python 3.10, 3.12 and 3.13.

---

## 20. Deployment and operations

| Target | How |
|---|---|
| Personal computer | `leadpipe service install` (§4) |
| Linux server | `pip install -e .`, `leadpipe init`, `leadpipe service install` + `loginctl enable-linger`, or `deploy/leadpipe.service` as a system unit |
| Docker | `docker compose up -d` (mounts `config.yaml`, `.env`, `./data`; needs `DASHBOARD_PASSWORD` because the container listens on 0.0.0.0; port bound to 127.0.0.1) |
| Public URL | reverse proxy with HTTPS (`deploy/Caddyfile.example`), set `dashboard.public_base_url`, `DASHBOARD_PASSWORD`, `BOOKING_WEBHOOK_TOKEN` |

**Operations:** health check `GET /health`; logs in `data/logs/leadpipe.log` (`leadpipe service logs`); state in
`data/leadpipe.db` (back it up; copy it while the engine is stopped or use `sqlite3 .backup`); upgrade with
`git pull && pip install -e .` and restart (migrations run automatically); emergency stop `leadpipe sending pause` or the
Home page Pause button; one engine per database (lock file).

**Regenerating the PDF guides:** `python scripts/build_docs.py` writes `docs/pdf/*.html` and, if Playwright for Python
is installed (`pip install playwright && playwright install chromium`), the PDFs. Otherwise open the HTML files in a
browser and use *Print → Save as PDF*.

---

## 21. Conventions and contributing

- Keep `python -m pyflakes leadpipe tests` and `pytest` clean; add a test for every behaviour change.
- New settings go into `default_config.yaml` **with a comment**, and into the user guide if users should know them.
- No secrets in the repository: `.env`, `config.yaml` and `data/` are git-ignored.
- User-facing text (UI, messages, guides) uses plain words; outbound copy must follow the playbook rules and pass
  `compliance.check_message`.
- Never add code that sends WhatsApp/social messages or places calls automatically; channels other than email stay
  one-click tasks.
- Database changes: add columns through `db.MIGRATIONS`, never by editing existing `CREATE TABLE` statements for live
  installs.
