# Architecture (for developers)

Python 3.10+, SQLite (WAL), Flask + Waitress, `requests` + BeautifulSoup, `dnspython`, `phonenumbers`,
`ruamel.yaml` (comment-preserving settings writer), the Anthropic SDK (optional). Everything optional degrades gracefully.

## Process model

`leadpipe start` = one process: the **engine** loop in a background thread + the **dashboard** (Waitress) in the main
thread, sharing one `DB` (thread-safe via an `RLock`; WAL lets a separate `leadpipe dashboard` process work too).
A PID lock (`data/engine.lock`) guarantees one engine per database; a second `start` exits with "already running".
`start --background` logs to `data/logs/leadpipe.log` (rotating) and is what the OS service runs (`service.py`):
Windows Task Scheduler XML (log-on trigger + 5-minute "start if not running" trigger, `IgnoreNew`, `WakeToRun`, no time
limit, battery allowed, `pythonw` = no window), macOS LaunchAgent (`KeepAlive`), Linux systemd user unit (`Restart=always`).
While running, `keep_awake()` blocks system sleep (Windows `SetThreadExecutionState`, macOS `caffeinate`, Linux `systemd-inhibit`).

## Engine cycle (`engine.py`)

Every `engine.tick_seconds` (60):

1. reload `config.yaml`/`.env` if changed; stop here if `engine_paused`
2. `handler.process_inbox`: IMAP for every mailbox (read-only), match, classify, transition, draft reply, notify *(5 min)*
3. `pipeline.process_timers`, `onboarding.tick`
4. `sender.send_due`: ≤ 1 email per mailbox per tick; random gap per mailbox; caps/warm-up per mailbox
5. `pipeline.enroll_new_contacts` + `writer.write_due` *(10 min)*: picks the channel per step, creates email drafts or To-do tasks
6. `enrich.enrich_pending` *(15 min)*; `discovery.run_discovery` when fresh leads < `reach.min_ready_leads` *(≥ 30 min apart)*
7. Google Sheets re-sync *(6 h)*; daily digest

Every job is wrapped: an exception is logged to `events` and never stops the loop.

## Data model (`db.py`)

| Table | Purpose |
|---|---|
| `areas` | the rollout queue: name, country, timezone, bbox, `position`, `status` (pending/active/done/skipped/failed), `tile`/`tiles`, `found` |
| `companies` | business + channels (phone, whatsapp, instagram, linkedin, twitter, facebook), rating/reviews, `priority`, `signals` (JSON evidence), `problems`, `score`, `status`, `area_id`, `tech` |
| `contacts` | a person (email) or a `social` contact (no email): **`stage`**, **`sequence`**, **`step`**, `next_action_at`, `mailbox`, `call_attempts` |
| `messages` | every outbound draft/task and inbound reply: `channel` (email/whatsapp/instagram/linkedin/twitter/facebook/call), `kind` (sequence/reply/onboarding/inbound), `status`, `action_url` (one-click link), `outcome` (calls), `mailbox` |
| `deals` | won deals + onboarding `checklist` / `reminders` |
| `suppression` | do-not-contact: email, `@domain`, phone (E.164 digits), profile URL, or `sha256:` hash |
| `events`, `seen_inbound`, `kv` | audit log, processed inbound ids, runtime switches/counters |

Columns added after 1.0 are applied by `MIGRATIONS` (`ALTER TABLE ADD COLUMN`) on every start, so old databases upgrade.

## Discovery (`discovery/`)

`seed_areas()` builds the queue from `reach.start_area` + `reach.next_areas` + (world rollout) `geo.WORLD_CITIES`
for the home country, then `reach.countries`, skipping `skip_countries` / `restricted_countries`. `current_area()`
activates the next pending area. `run_discovery()` geocodes once (Nominatim → bbox, country, `geo.timezone_for` from
tzdata's zone table), then searches **one tile** of an n×n grid per step (OSM Overpass), plus Serper / Google Places
text search on the first tile. When all tiles are done the area is `done`. `save_company()` dedupes by source id,
domain, or name+city, merging new details (phones, socials, priority) into existing rows.

`csv_import.py` maps headers loosely (incl. Google-Maps-scraper names), turns Google Sheets links into CSV export links,
normalises phones, derives country/timezone, and supports `priority`.

## Research (`enrich/`)

`website.audit_website()` fetches the homepage + ranked sub-pages and records signals with evidence, emails (mailto,
Cloudflare-protected, `[at]`/`[dot]`), people (cards, sentences, JSON-LD), social links, `tel:` numbers, tech stack,
footer agency credit, stale blog, schema markup, ATS job-board roles (Greenhouse/Lever/Ashby/Workable public feeds),
startup/funding lines. `patterns.py` learns the address format from a published address of a named person and applies
it to the owner (refuses ambiguous cases). `enrich_company()` ranks/creates up to N contacts (status `valid`/`risky`/
`pattern`/`guess`), creates a `social` contact when there is no usable email but a phone/profile, scores, qualifies.

## Channels and tasks (`channels.py`, `writer.py`, `tasks.py`)

`channels.available()` lists usable channels for a lead (mobile numbers → WhatsApp); `pick_channel()` resolves a
step's channel (`email` → falls back to a DM, `dm` → `channels.dm_order`, then call). `write_due()` skips steps with
no channel, limits extra calls/DMs on email leads to scores ≥ `min_score_for_tasks` (or high-value), caps tasks per
day (postponing the rest), and stores `action_url` (wa.me with prefilled text, ig.me, m.me, profile URLs, `tel:`).
Only `channel='email'` messages are ever sent by `sender.py`; the rest are To-do tasks. `tasks.call_outcome()` /
`done()` / `log_reply()` close the loop; answered calls and logged DM replies go through the same `handler.apply_intent`
as email replies (with `channel_hint`, so the reply is drafted on the same channel).

## State machine (`pipeline.py`)

`STAGE_SEQUENCE` maps stage → track; `sequence_for()` sends no-email leads in `cold` to `cold_social`. `set_stage()`
cancels queued sequence messages (all queued messages for terminal stages) and starts the new track.
`advance_after_send()` / `skip_step()` move along the track keeping the spacing relative to the previous touch.

## Writing (`writer.py`, `templates.py`, `llm.py`)

`build_context()` turns company + contact + profile into variables (top two signals → observation / cost / fix / topic,
proof (per category), quick win, call-script fields). With AI, the same facts + a purpose brief + the thread go to Claude
(JSON schema output, effort from config, server-side refusal fallback), re-checked by `compliance.check_message` and
retried once before falling back to templates. The `claude` CLI backend runs in an empty temp directory with every
isolation flag the installed CLI supports (`--safe-mode`, `--tools ""`, `--setting-sources ""`, `--strict-mcp-config`,
`--no-session-persistence`, `--disable-slash-commands`), prompt on stdin. `llm.max_calls_per_day` is enforced via `kv`.

## Mailboxes (`mailboxes.py`, `sender.py`)

The main mailbox comes from `.env`; extra ones from `config.yaml → mailboxes` (passwords via `password_env`). Per
mailbox: cap, warm-up start (`kv first_send_date:<name>`), gap (`kv next_send_at:<name>`). A contact is pinned to the
mailbox that first emailed it.

## Extending

- **Lead source:** add `discovery/<name>.py` returning dicts (`name, website, phone, email, city, category, rating,
  reviews, lat, lon, source, source_ref`) and call it in `run_discovery`.
- **Signal:** detector in `enrich/website.py` (evidence in `audit.signals[key]`), copy + topic in `templates.SIGNALS`/`TOPICS`, weight in `default_config.yaml`.
- **Step type:** variants in `SEQUENCE_TEMPLATES` (or `CALL_TEMPLATES`), a brief in `PURPOSE_BRIEFS`, a word limit in `compliance.WORD_LIMITS`.
- **Intent:** `classifier.INTENTS` + rules, a reply in `REPLY_TEMPLATES`, a transition in `handler.apply_intent`.

## Tests

`pytest` (40 tests, no network): rules, classifier, verification, website audit against a local HTTP server, full
journeys in test mode, channels, world rollout (mocked map), Maps-scraper import → WhatsApp task, email patterns,
multiple mailboxes, call outcomes, task quotas, service definitions, settings writer, dashboard pages/CSRF/auth/setup form.
