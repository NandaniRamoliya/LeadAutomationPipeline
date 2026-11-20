# Architecture (for developers)

Python 3.10+, SQLite (WAL), Flask + Waitress, `requests` + BeautifulSoup, `dnspython`, the Anthropic SDK (optional).
No external services are required; everything optional degrades gracefully.

## Process model

`leadpipe start` = one process: the **engine** loop in a background thread + the **dashboard** (Waitress) in the main
thread, sharing one `DB` object (thread-safe via an `RLock`; SQLite WAL also lets a separate `leadpipe dashboard`
process read/write concurrently). A PID lock file (`data/engine.lock`) prevents two engines on one database.

## Engine cycle (`engine.py`)

Every `engine.tick_seconds` (default 60):

1. `handler.process_inbox`: IMAP fetch (read-only, `BODY.PEEK`), match, classify, transition, draft reply, notify *(every 5 min)*
2. `pipeline.process_timers`: finished tracks → `on_complete`; `meeting` → `proposal` after the call
3. `onboarding.tick`: auto-tick sent steps, draft next onboarding email / reminders
4. `sender.send_due`: at most one email per call; next allowed send time = now + random gap
5. `pipeline.enroll_new_contacts` + `writer.write_due` *(every 10 min)*
6. `enrich.enrich_pending` and, if the queue is low, `discovery.run_discovery` *(every 20 min / 6 h)*
7. daily digest

Each job is wrapped so an exception is logged to `events` and never stops the loop. Job timestamps live in `kv`.

## Data model (`db.py`)

| Table | Purpose |
|---|---|
| `companies` | one business: website, domain, category, timezone, `signals` (JSON with evidence), `problems`, `score`, `status` (new/qualified/disqualified/error) |
| `contacts` | a person (or a manual-channel placeholder): email + `email_status`, **`stage`**, **`sequence`**, **`step`**, `next_action_at` |
| `messages` | every outbound draft/sent email and inbound reply: `kind` (sequence/reply/onboarding/inbound), `status` (pending/approved/sent/failed/cancelled/rejected/received), `purpose`, `check_issues`, `message_id`, `intent` |
| `deals` | won deals + onboarding `checklist` and `reminders` (JSON) |
| `suppression` | do-not-contact: `email`, `@domain`, or `sha256:<hash>` (erased people) |
| `events` | audit log shown in the dashboard |
| `seen_inbound` | processed inbound Message-IDs that matched no lead |
| `kv` | runtime switches (`sending_paused`, `autopilot_*`), counters, heartbeat, secrets |

## State machine (`pipeline.py`)

`STAGE_SEQUENCE` maps stage → track. `set_stage()` cancels queued sequence mail (all queued mail for terminal stages),
starts the new track (`next_action_at = start + steps[0].day`), and suppresses on unsubscribe/bounce.
`advance_after_send()` moves `step` forward and schedules the next step relative to the previous send.
Messages carry `(sequence, step)`; the sender cancels any message whose contact has moved on.

## Writing (`writer.py`, `templates.py`)

`build_context()` turns company + contact + profile into template variables (greeting, top two signals → observation /
cost / fix, proof, quick win...). With AI, the same facts + `PURPOSE_BRIEFS` + thread history go to Claude with a JSON
schema (`output_config.format`), effort from config, and server-side refusal fallback; output is re-checked by
`compliance.check_message` and retried once before falling back to the template.

## Reply understanding (`classifier.py`, `handler.py`)

Rules first for bounce / auto-reply / unsubscribe (deterministic, safety-critical), then AI (if configured, confidence
≥ 0.5) or keyword rules for the rest. Quoted history is stripped before classifying. Matching order: `In-Reply-To` /
`References` → DSN recipients → sender address → same company domain (creates a colleague contact).

## Extending

- **New lead source:** add `discovery/<name>.py` returning dicts with `name, website, phone, email, city, category,
  source, source_ref`, then register it in `run_discovery`.
- **New signal:** add a detector in `enrich/website.py` (put evidence in `audit.signals[key]`), copy in
  `templates.SIGNALS`, a weight in `default_config.yaml → signals`.
- **New follow-up step type:** add variants to `SEQUENCE_TEMPLATES`, a brief to `PURPOSE_BRIEFS`, a word limit to
  `compliance.WORD_LIMITS`, then use it in `sequences:`.
- **New intent:** add to `classifier.INTENTS` + rules, a reply in `REPLY_TEMPLATES`, a transition in `handler.apply_intent`.

## Tests

`pytest` runs unit tests (rules, classifier, verification, website audit against a local HTTP server) and end-to-end
journeys in dry-run mode (lead → cold → reply → warm → meeting → proposal → won → onboarding → client care; no-reply →
lost; not-now → nurture; unsubscribe; not-interested courtesy reply; sender guards; dashboard, CSRF, auth, unsubscribe
link, booking webhook). DNS is stubbed in `tests/conftest.py`; no network is needed.
