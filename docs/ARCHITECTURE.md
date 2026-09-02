# Architecture

## Process model

One process serves everything. FastAPI handles the HTTP API and the static UI;
two background threads do the slow work:

```
uvicorn (async)
 ├── HTTP handlers ──────────── SQLAlchemy ──► SQLite (leadgen_state/leadgen.db)
 ├── ScrapePipeline thread ──── scrape → dedupe → enrich → score → (save on demand)
 └── DispatchEngine thread ──── plan → compliance gate → SMTP → persist
```

Both workers own their own sessions via `session_scope()`, so a request handler
never shares a session with a worker. All state lives in SQLite — the in-memory
job/engine objects are only progress caches, and a restart resumes from the
database (queued messages stay queued, sent messages stay sent).

## Data model

| Table | Purpose |
| --- | --- |
| `email_accounts` | Sending credentials (encrypted), SMTP/IMAP endpoints, per-account caps, verification state |
| `campaigns` | Targeting + offer + copy style + pacing, bound to a sender |
| `leads` | Curated prospects: contact data, source, score + reasons, status, pipeline stage, `selected` flag |
| `outbound_messages` | One row per generated email: subject/body (stored, so sends are auditable and reproducible), RFC Message-ID, thread id, status, delay used, compliance score |
| `replies` | Inbound messages, matched lead/message, intent, sentiment, match method, IMAP UID |
| `activities` | Timeline: outbound, reply, note, stage change |
| `suppressions` | Global do-not-contact list, enforced on every send path |
| `sync_state` | Watermarks (e.g. last IMAP sync per account) |

`Lead.selected` is the gate: the dispatcher only ever materialises messages for
selected leads with an email address that is not suppressed.

## Targeting

`GeoService` loads the bundled dataset lazily (one JSON file per country) and
expands a selection into concrete `Place` records. `"*"` is honoured at every
level, so `{country: "US", state: "AZ", city: "*"}` becomes every Arizona city,
and `{country: "*", state: "*", city: "*"}` becomes the whole dataset (capped).

`NicheAdvisor` scores each place against the matched archetype:

```
score = 30
      + 12 per wanted climate tag present        (max 36)
      - 20 per avoided climate tag present
      + up to 34 for exceeding the temperature threshold (or -24 for missing it)
      + 8 + 5 per density tier above the archetype minimum
      ± 10/8 for the archetype's region bias (mature B2B service markets)
```

The result is sorted by score, then density, then name — never alphabetical by
accident. Every suggestion carries its reasons, which is what the UI shows in
the "Why" column.

## Copy generation

`Copywriter.generate_offline()` is the always-available path:

1. pick a template family (or the campaign's), seeded by `sha1(lead_id|email|template)`;
2. resolve spintax (`{a|b|c}`) with that seed;
3. fill merge tags from lead + campaign + archetype hooks;
4. append only the offer blocks that are toggled on;
5. append the CAN-SPAM footer (postal address + opt-out).

Because the seed is stable per lead, regenerating the same lead yields the same
email — which is what makes an audit of "what did we actually send" possible.

`generate_with_llm()` sends a structured brief (business facts, our service,
tone, offers, hooks, hard rules) and expects JSON back. If the LLM is
unconfigured, errors, or returns something too short, the template path is used
and the reason is recorded on the copy object.

## Dispatch loop

```
prepare_queue()   → generate + store one OutboundMessage per selected lead
DelayPlanner.plan() → shuffle order, assign gaps, insert long breaks, defer past the daily cap
loop:
    check stop/pause
    recount sent_today / sent_this_hour from the DB   ← live, not cached
    ComplianceEngine.full_check(content + behaviour)
        blocked + resumable (cap/quiet/burst) → sleep until allowed
        blocked + permanent (suppressed)      → mark skipped, move on
    sleep the slot's gap (0.5s slices so pause/stop are instant)
    SMTP send → update message, lead, activity
    consecutive-failure circuit breaker
```

Quota is recomputed from the database before every send rather than tracked in a
counter, so two runs, a restart or a manual send cannot drift the accounting.

### The burst check runs *before* the gap is slept

This ordering is load-bearing. The compliance check happens before the
dispatcher sleeps the slot's humanised gap, so at the moment of the check the
last send may be only milliseconds old. `check_behaviour()` therefore takes the
gap that is about to be slept as `pending_gap_seconds` and measures the interval
as `elapsed + pending_gap`. Without that, the burst guardrail sees a ~0s gap and
blocks every send after the first with "Only 0s since the last send".

Two pacing knobs interact here:

* `DelayConfig.min_seconds` — the campaign's own pacing, passed in as
  `min_gap_seconds`. **The campaign governs**, so a 10s campaign gap is honoured
  even though the global default is 45s.
* `Settings.min_delay_seconds` — the global default, used only when a campaign
  does not set one. A hard 5s floor applies regardless.

The hourly cap is likewise clamped to the effective daily cap when the config is
built, otherwise a campaign capped at 50/day with a 60/hour global setting
reports an impossible configuration.

## Reply matching

`InboxSyncService.ingest()` is deliberately transport-free — it takes parsed
`FetchedMessage` objects, which is why it is unit-testable without a mail server.
`ImapInbox` is the only part that talks IMAP, and it opens the mailbox read-only.

Matching precedence: `In-Reply-To`/`References` → normalised subject → sender
address. The method used is stored in `replies.matched_by`.

## Extension points

* **New scraper** — subclass `BaseScraper`, implement `search()`, register in
  `SCRAPER_REGISTRY`. You inherit polite delays, `robots.txt` checks and the
  enrichment/scoring pipeline.
* **New copy template** — append a `Template` to `TEMPLATES` in `copywriter.py`.
* **New niche archetype** — append an `Archetype` to `ARCHETYPES`; targeting,
  query planning and hooks all pick it up.
* **New compliance rule** — add a check to `ComplianceEngine.check_content()` or
  `check_behaviour()`; `block` severity stops the queue, `warn` lowers the score.
