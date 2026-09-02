# LeadGen Studio

A locally hosted B2B lead-generation, cold-outreach and lightweight CRM platform.
It scrapes business contacts for a niche + geography you choose, writes a
**different personalised email per lead**, dispatches them with randomised,
human-shaped delays inside Google's sending limits, then watches the inbox for
replies and moves responders into a pipeline.

Everything runs on your own machine. Credentials are encrypted with a
locally generated key, the database is a SQLite file, and no data leaves the
machine unless you configure an LLM endpoint yourself.

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌───────────┐
│ 1 Targeting  │ → │  2 Scraping  │ → │  3 Compose   │ → │ 4 Dispatch   │ → │  5 CRM    │
│ niche + geo  │   │ + curation   │   │ + compliance │   │ paced sends  │   │ replies   │
└──────────────┘   └──────────────┘   └──────────────┘   └──────────────┘   └───────────┘
```

---

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env            # set your business name + postal address
python -m leadgen doctor        # sanity-check config, dataset and scrapers
python -m leadgen serve         # http://127.0.0.1:8765
```

To see the whole product populated before you connect anything real:

```bash
python -m leadgen demo          # seeds a campaign with leads, sends and replies
```

Then open the app, pick the demo campaign, and walk through the tabs.

### Try the CLI pieces directly

```bash
python -m leadgen suggest "HVAC and AC repair services"
python -m leadgen suggest "managed IT support" --country US --top 8
python -m leadgen preview --offering "commercial HVAC maintenance contracts" \
    --business "Desert Air Conditioning" --city Phoenix --demo-call --slots 3
```

---

## The five modules

### 1 · Smart targeting & configuration

* **Account integration** — add sending addresses (Gmail, Google Workspace,
  Microsoft 365, Zoho, Fastmail or any SMTP host). App passwords are encrypted
  with a Fernet key generated on first run into the git-ignored state directory,
  never stored in clear text, and never returned by the API.
* **Granular geolocation** — a bundled dataset of **54 countries, 268
  states/provinces and 685 cities**, each carrying climate tags, an average
  summer temperature and a market-density tier. Drill to a single city, pick a
  whole state, or take the macro **"All"** at any level. Free-text cities cover
  anything not in the dataset.
* **AI-driven niche suggestions** — 18 service archetypes map an offer onto
  climate, density and buyer personas. Ask for *"HVAC/AC repair"* and it ranks
  Phoenix, Dubai, Doha, Riyadh and Kuwait City at the top with the reasoning
  attached (`avg 41°C summer, +11°C vs the 30°C threshold`). *"Snow removal"*
  returns Anchorage, Calgary, Helsinki, Oslo. *"Managed IT"* returns dense
  service-mature metros. When an LLM key is configured the model refines hooks,
  categories and subject angles — its output is **merged into** the rule result,
  never used as the sole source, so a model outage cannot break targeting.
* **Offer customisation** — toggle a free demo call, free audit, case study,
  discount %, limited slots, guarantee wording, local reference or a
  low-pressure sign-off. Toggled blocks are woven into the copy; untoggled ones
  are simply not mentioned.

### 2 · Automated scraping & lead curation

* **Pluggable sources** — `duckduckgo` (no key), `google_places` (key, highest
  quality: name, address, phone, website, rating and review count in one call),
  `csv` (paste a list), `demo` (deterministic offline sample data, labelled).
* **Query planning** — queries are built from the matched archetype's search
  terms crossed with your selected places.
* **Enrichment** — visits each website to recover an email address (preferring
  personal addresses over `info@`), a phone number, and runs an MX check.
* **Scoring** — 0–100 from email quality, social proof, buyer signals in the
  snippet and city match, with the reasons stored per lead.
* **Lead review dashboard** — filter, search, **Select All**, exclude or delete
  in bulk, export CSV. Nothing unselected is ever contacted.

### 3 · Dynamic content & compliant dispatch

* **Per-lead copy** — four template families with spintax subject rotation.
  Variation is seeded by lead id, so a given lead always gets the same variant
  (reproducible and auditable) while the campaign stays varied. Every email
  carries the CAN-SPAM footer: postal address + opt-out.
* **Randomised, unordered delays** — the send order is shuffled, gaps are drawn
  from a triangular distribution (mostly short, occasional long tail), and a
  **long break of 10–25 minutes** is inserted every 12 sends. No fixed cadence to
  fingerprint.
* **Policy guardrails** — daily cap of **400 recipients** (Google's free-account
  ceiling is 500), hourly cap of 60, a minimum gap since the last send, optional
  quiet hours, a global suppression list, per-domain concentration warnings, and
  a circuit breaker after 5 consecutive failures. Every send is re-checked
  against live quota immediately before it goes out.
* **Headers** — proper `Message-ID`, `List-Unsubscribe` (+ one-click POST),
  `Precedence: bulk`, `Auto-Submitted`, and multipart text/HTML bodies.

### 4 · Integrated CRM & response monitoring

* **Inbox syncing** — IMAP (read-only: nothing is flagged or deleted) across
  every active account.
* **Reply detection** — matched by `In-Reply-To`/`References` against the stored
  Message-ID first, then by subject, then by sender address. The match method is
  recorded so you can see *why* a reply was linked.
* **Intent classification** — deterministic rules for interested / not
  interested / out-of-office / auto-reply / question / spam. Bounces are never
  mistaken for interest; an unsubscribe always wins.
* **Pipeline** — new → contacted → replied → engaged → meeting → proposal →
  won/lost, with notes, an activity timeline and reply-rate metrics per campaign.
* **Opt-outs** — any unsubscribe reply adds the address to the global
  suppression list and marks the lead unsubscribed across every campaign.

---

## HTTP API

The UI is a thin client over a documented JSON API.

| Area | Endpoints |
| --- | --- |
| Accounts | `GET/POST /api/accounts`, `PATCH/DELETE /api/accounts/{id}`, `POST /api/accounts/{id}/test` |
| Targeting | `GET /api/targeting/countries`, `.../states`, `.../cities`, `GET /api/targeting/search`, `POST /api/targeting/expand`, `POST /api/targeting/niche-suggestions` |
| Campaigns | `GET/POST /api/campaigns`, `GET/PATCH/DELETE /api/campaigns/{id}` |
| Leads | `GET /api/campaigns/{id}/leads`, `PATCH .../leads/{lid}`, `POST .../leads/bulk`, `GET .../leads-export.csv` |
| Scraping | `POST /api/campaigns/{id}/scrape`, `GET /api/campaigns/scrape-jobs/{job}`, `POST .../save`, `POST .../cancel` |
| Copy | `POST /api/campaigns/{id}/preview`, `POST /api/campaigns/preview-sample`, `POST /api/campaigns/compliance-check` |
| Dispatch | `POST /api/campaigns/dispatch/prepare\|start\|pause\|resume\|stop`, `GET .../dispatch/state`, `POST /api/campaigns/{id}/plan-preview` |
| CRM | `GET /api/crm/overview`, `/replies`, `/pipeline`, `/suppressions`, `POST /api/crm/sync`, `POST /api/crm/leads/{id}/stage\|notes` |
| System | `GET /api/system/health`, `/settings`, `/compliance-posture`, `/scrapers` |

Interactive docs: `http://127.0.0.1:8765/api/docs`.

---

## Configuration

Copy `.env.example` to `.env`. Every key is prefixed `LEADGEN_`.

| Key | Default | Notes |
| --- | --- | --- |
| `LEADGEN_HOST` / `LEADGEN_PORT` | `127.0.0.1` / `8765` | Binds to localhost by default because it holds credentials |
| `LEADGEN_DAILY_RECIPIENT_CAP` | `400` | Google free accounts stop at 500 |
| `LEADGEN_HOURLY_RECIPIENT_CAP` | `60` | Prevents burst fingerprints |
| `LEADGEN_MIN_DELAY_SECONDS` / `MAX_DELAY_SECONDS` | `45` / `240` | Random gap window |
| `LEADGEN_LONG_PAUSE_EVERY` | `12` | Long break after N sends |
| `LEADGEN_ENFORCE_QUIET_HOURS` | `false` | With `QUIET_HOURS_START`/`END` |
| `LEADGEN_BUSINESS_NAME` / `BUSINESS_MAILING_ADDRESS` | — | Required in every email by CAN-SPAM |
| `LEADGEN_UNSUBSCRIBE_URL` | — | Optional; falls back to "reply STOP" |
| `LEADGEN_LLM_PROVIDER` / `_API_KEY` / `_MODEL` / `_BASE_URL` | `offline` | Optional AI copy engine |
| `LEADGEN_GOOGLE_MAPS_API_KEY` | — | Optional Places source |

---

## Tests

```bash
pytest                 # 161 tests
pytest -q tests/test_delay.py tests/test_compliance.py
```

Covers geo expansion and the `*` macro, niche ranking, delay randomness and
caps, every compliance rule, copy personalisation and offer weaving, email
extraction and scoring, CSV import, reply classification/matching/ingestion, and
the full API flow including a dry-run dispatch.

There is also cover for the parts that are easy to break silently:

- `tests/test_frontend.py` runs `node --check` on every shipped JS file (a parse
  error in one script blanks the whole app) and, when `jsdom` is installed,
  mounts `index.html` headlessly and renders all eight screens against API
  responses captured from a live server — 46 DOM assertions including the
  country → state → city drill-down.
- `tests/test_dependencies.py` walks the AST of every module and asserts each
  third-party import is declared in `pyproject.toml`, so a missing dependency
  cannot ship.

Capture the UI fixtures against a running server with:

```bash
python -m leadgen demo
python -m leadgen serve &
tests/js/capture_fixtures.sh
```

---

## Legal and ethical use

Cold email is legal in most jurisdictions **only if** you follow the rules, and
this app is built around them rather than around evading them. Before you send
anything, read [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md). In short:

* B2B only, and only to addresses published by the business for business contact.
* Every email carries your real postal address and a working opt-out (enforced by
  a blocking compliance check — mail without them will not send).
* Honour every opt-out immediately and permanently (the suppression list does
  this automatically).
* GDPR / PECR / CASL apply to EU, UK, Canadian and other recipients and require
  a lawful basis or consent — for those regions, prefer opt-in lists.
* Respect `robots.txt` and the terms of service of any site you scrape. Scraping
  public listings is not the same as having permission to market to the people on
  them; keep volumes modest and identify yourself honestly.
* Never buy or use harvested consumer addresses, and never disguise the sender.

Abusing this will get your domain blacklisted and may be illegal. The guardrails
exist to keep you inside the rules, not to help you skirt them.

## Layout

```
leadgen/
  app.py              FastAPI factory (API + static UI)
  cli.py              serve / demo / suggest / preview / doctor
  config.py           env-driven settings
  db.py, models.py    SQLAlchemy + SQLite schema
  security.py         local Fernet credential vault
  routers/            accounts, targeting, campaigns, crm, system
  services/
    geo.py            country -> state -> city dataset + '*' expansion
    niche_advisor.py  18 service archetypes + scoring + optional LLM
    copywriter.py     per-lead templates, offer weaving, LLM path
    compliance.py     content rules + sending-behaviour guardrails
    delay.py          randomised unordered pacing, caps, quiet hours
    dispatcher.py     the sending engine
    sender.py         SMTP transport, headers, provider presets
    inbox_sync.py     IMAP polling + reply matching
    classifier.py     reply intent heuristics
    scrapers/         duckduckgo, google_places, csv, demo, enrich, pipeline
  data/               spam_rules.json + geo/*.json (54 countries)
  static/             dependency-free SPA (no build step)
  demo_data.py        seeds a worked example (`leadgen demo`)
scripts/build_geo.py  regenerates the geo dataset
scripts/seed_demo.py  thin wrapper around leadgen/demo_data.py
docs/                 ARCHITECTURE.md, COMPLIANCE.md
tests/                161 tests, incl. tests/js/ui.test.js (jsdom)
```

## Licence

MIT.
