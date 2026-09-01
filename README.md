# Logicon Outbound Research Engine

An automated lead-research pipeline for B2B cold outreach. Point it at
an industry, and it produces a spreadsheet of real companies, the one
person at each company who actually owns the relevant workflow (not
just any employee), a verified work email for them, and the specific
fact about that company used to justify why they're worth contacting.
It is not a scraper and not a mass-emailer -- it's the judgment layer
that decides *who* to contact and *why*, sitting between raw company
lists and a cold-email tool (Woodpecker, Instantly, etc.).

**What makes it different from a generic scraper:** every company that
gets excluded is excluded with a stated, inspectable reason -- wrong
industry, too small, a subsidiary of an already-contacted parent
company, no one in a qualifying role. Nothing is guessed. A missing
fact (an email, a headcount, a metro count) stays blank with a reason
rather than being interpolated or faked, and every dollar of paid
enrichment is spent on companies already shown likely to qualify, never
on the raw, unfiltered pool.

Built and proven end-to-end on one vertical: **court reporting
agencies**. The engine itself is fully generic -- industry-specific
logic (job-title patterns, disqualifiers, research questions) lives in
one config file per vertical (`backend/campaigns/configs/`), so a new
industry is a new config file, not a rewrite. `process_serving` and
`ia_ime` are wired into the config registry and ready to receive their
own config, intentionally not built out yet -- the plan was to prove
one vertical fully on real data before replicating the pattern.

## Requirements

- Python 3.10+
- A [Clay](https://clay.com) workspace with API access (Clay does the
  actual data enrichment; this app owns the business logic and never
  replaces it) -- a free/trial workspace is enough to try it
- Optional: a Google Maps Platform API key, to add Google Places as an
  extra source (the pipeline runs fully without it)

## How it works

Seven stages, split deliberately into a free/local qualification phase
and a paid enrichment phase, so a limited API budget is always spent on
the companies already shown likeliest to qualify -- never on the whole
raw pool:

1. **Source** -- pull raw companies from however many collectors are
   configured for the vertical (directories, Clay's own search
   database, Google Places, manual CSV/Excel imports).
2. **Dedupe** -- collapse the same company seen from multiple sources
   into one record, by domain first, name+location as a fallback.
   Dedup state persists across runs, so re-running a source never
   reprocesses a company it already has.
3. **Qualify** -- firmographic and text-based disqualifiers (company
   size, industry, roll-up/subsidiary detection) run for free, before
   anything paid.
4. **Coordinator search** -- find staff whose title matches the
   vertical's qualifying role (e.g. "scheduling coordinator" for court
   reporting), still free, and use the result to assign a tier
   (A/B/C).
5. **Research** -- *only* for Tier A/B companies, paid per-field Clay
   calls answer the vertical's specific research questions (e.g. how
   many metro areas a company covers).
6. **Decision-maker + email** -- find the actual person, verify they
   currently work there, look up a real work email. Every step is
   idempotent, so re-running a batch never re-pays for a person already
   resolved.
7. **Suppress + export** -- check a do-not-contact list, then write out
   four CSVs: ready-to-send, full research trail, rejected-with-reasons,
   and a random QA sample for a human spot-check before anything goes
   out.

## Setup

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:
- `CLAY_API_KEY` -- Clay -> Settings -> Account -> API keys (beta)
- `GOOGLE_MAPS_API_KEY` -- optional, omit to skip Google Places entirely

## Clay setup (one-time, in Clay's UI)

Clay's Public API (`api.clay.com/public/v0`) has no endpoint to create or
list functions -- routine ids come from Clay's UI, not from this script.

1. **Company-research custom function** (powers the playbook's Claygent-style
   research -- reporter_count, metro_count, hiring_signal, etc.):
   - In Clay, create a table with two input columns: `domain` (text) and
     `prompt` (text).
   - Add an AI/Claygent research column whose prompt is just `{{prompt}}`
     run against `{{domain}}`'s website, returning a single value (or the
     literal string `NONE` if not found).
   - Open the table -> **Functions** -> create a function from it -> **Details**
     -> enable **API** -> copy the `t_...` id into `CLAY_ROUTINE_RESEARCH_GENERIC`
     in `.env` as `function:t_xxxxxxxx`.
   - `scripts/validate_clay.py` tests this. If Clay's UI can't take a
     fully dynamic runtime prompt reliably, fall back to one function per
     research field instead (`CLAY_ROUTINE_RESEARCH_REPORTER_COUNT`, etc.
     in `.env.example`) -- same principle, more setup, still config-driven.

2. **Clay-managed functions** (Work Email, Employee Count, Revenue, Website
   Techstack, Company Job Openings, Company News): each has its own routine
   id in your workspace. Add the ones you want to `.env`
   (`CLAY_ROUTINE_WORK_EMAIL`, `CLAY_ROUTINE_EMPLOYEE_COUNT`, ...). Any left
   blank are reported as unavailable, never guessed.

**Multiple Clay accounts (optional):** functions are per-workspace, so
switching Clay accounts means switching both the API key and every
routine id at once. `CLAY_ACTIVE_ACCOUNT` picks which complete set is
used -- unset (or `primary`) reads the bare `CLAY_*` vars above; any
other name reads `CLAY_ACCOUNT_{NAME}_*` instead (see
`.env.example` and `backend/providers/clay/accounts.py`). This is how
the pipeline adds capacity from a second workspace, or fails over when
one account's plan limits are hit, without any code change.

Then validate:

```bash
python scripts/validate_clay.py
python scripts/validate_google_places.py   # only does anything if GOOGLE_MAPS_API_KEY is set
```

This calls the real workspace (minimal test calls against one test
domain) and reports exactly which of the 7 required capabilities
(auth, company enrichment, company research, people search, person
data, work-email discovery, custom research routine) are available.
Nothing is invented if a capability isn't configured yet.

## Run a campaign

```bash
python run_campaign.py --campaign court_reporting --target 50
```

`--target` is the number of **final campaign-ready prospects**, not raw
companies -- the pipeline stops launching new batches once that many
Tier A/B prospects with a verified employment + usable email exist (plus
a small buffer, `TARGET_BUFFER_PCT` in `.env`).

Outputs land in `data/exports/`:
- `{campaign}_{date}_ready.csv` -- Woodpecker-ready (Tier A/B, verified employment, usable email, no duplicates)
- `{campaign}_{date}_research.csv` -- every research value + evidence + source for every qualified company
- `{campaign}_{date}_qa_sample.csv` -- 20 random final prospects for human spot-check before upload
- `{campaign}_{date}_rejected.csv` -- everything that fell out of the funnel and why (source-quality/ICP visibility)

## Architecture

```
run_campaign.py
backend/
  campaigns/            # engine.py (the one pipeline) + configs/*.py (per-vertical data)
  sources/               # company-only collectors, no qualification logic
    court_reporting/ncra.py, state_associations.py, clay_icp_search.py, ...
    google_places.py     # optional, skipped cleanly with no GOOGLE_MAPS_API_KEY
  providers/clay/         # ALL Clay-specific code; everything else uses normalized models
    accounts.py           # multi-account support -- swap API key + routine ids with one env var
  research/               # Claygent-style one-fact-per-call research + evidence + signal detection
  qualification/          # firmographics, coordinator classification, tiering, disqualifiers
  contacts/                # decision-maker discovery, employment verification, work email
  suppression/             # local CSV suppression (EMAIL/DOMAIN/COMPANY)
  credit/                  # budget + target-count-aware batch controller
  exports/                 # the 4 CSVs
  models/                  # Pydantic schemas + SQLAlchemy models (SQLite)
data/
  manual_imports/          # CSV fallback for sources without a live adapter yet
  exports/, logs/
  suppressions.csv
scripts/
  validate_clay.py, validate_google_places.py   # one-time Clay/Google setup checks
  export_all.py                                  # per-tier + full-database CSV exports
  source_report.py                               # collection counts by source
  build_court_reporting_master.py                # merges pipeline output with manually-researched contacts
```

## Sources -- current state (court_reporting)

- **NCRA PROLink**: live, working HTTP adapter (`backend/sources/court_reporting/ncra.py`).
  It's a plain ASP.NET form-postback page (confirmed by inspection, not
  JS-rendered), so this is a direct form submit + HTML parse -- no browser
  automation. It's an opt-in *individual* professional directory, not a
  clean company directory: each result is a member (name, address, phone,
  email); this adapter derives the company's domain from the member's
  email and treats that as the raw company (name is a domain-derived
  placeholder until Clay's company-domain enrichment fills in the real
  registered name). Yield per state is low (opt-in), so it runs across all
  50 states + DC by default.
- **State court reporter associations**: manual-CSV-import only for now
  (`data/manual_imports/court_reporting_state_associations.csv`). A couple
  of candidate state sites were checked during the build and weren't
  reliably reachable/parseable, so per the source-adapter rules this ships
  as an honest manual-import fallback rather than a scraper against an
  unverified endpoint. Populate the CSV (columns documented in
  `state_associations.py`) to add real rows; flip a state's `status` to
  `"live"` in `STATE_REGISTRY` once a real adapter is written and verified.
- **Google Places (New)**: optional, live, only runs if `GOOGLE_MAPS_API_KEY`
  is set. Text Search + Place Details, narrow FieldMask.

## Known limitations / things to confirm against the live Clay API

- **Employment verification is query-guaranteed, not independently
  cross-checked.** Clay's people-search response doesn't include a
  separate current-company-domain field to compare against -- the query
  itself filters on `company.domain = "<domain>" and is_current = true`,
  so any person returned is already, by construction, currently at that
  domain per Clay's own data. `contacts/employment.py::verify_employment`
  still runs the comparison (in case Clay's `is_current` flag is stale),
  but there's no second, independent source backing it up in this MVP.
- Clay's exact per-routine JSON response shape isn't published in the
  developer docs (depends on each function's configured output schema).
  `backend/providers/clay/routines.py::_extract_output` and
  `backend/providers/clay/search.py::_parse_person` are defensive/best-effort
  parsers; `scripts/validate_clay.py` prints the raw response keys so these
  can be tightened against a real response on first run.
- Clay's Public API has no endpoint to read the workspace's actual credit
  balance. The run report's "Clay usage" is a local counter of calls this
  run issued, checked against an optional `CLAY_MAX_BUDGET` ceiling you set
  -- not Clay's real account balance.
- HEADCOUNT_GROWTH, CALL_TO_SCHEDULE, ACQUISITION, and NEW_OFFICE buying
  signals are defined in the schema (and ACQUISITION/NEW_OFFICE are
  referenced in the court_reporting config's signal_rules as sourced from
  `company_news`) but not auto-detected yet in `research/evidence.py` --
  wiring them up means calling the `company_news` Clay-managed function and
  parsing its text, which isn't built yet. No reliable data source means
  they're left undetected rather than approximated, per the no-fabrication
  rule.

## Tests

```bash
./.venv/bin/pip install pytest
./.venv/bin/python3 -m pytest tests/ -v
```

94 tests, all pure-logic (no network/Clay calls, so they run the same on
any machine). Covers normalization, dedup (within and across
runs/sources), suppression, disqualifiers, coordinator classification,
tiering, and every idempotency guarantee described below.

## Engineering notes: real production bugs found and fixed

This system's real ICP was proven strict by running it against live
data repeatedly and treating every unexpectedly-low result as a bug to
disprove, not a number to accept. A few of the fixes that came out of
that, since they're a better signal of engineering process than any
description of the architecture:

- **Duplicate-coordinator inflation.** Coordinator search had no
  idempotency check, so re-processing a company could insert the same
  real person as a second/third row -- inflating that company's
  coordinator count and, downstream, its tier. Found by querying real
  results, not by inspection; fixed with an identity check before
  insert, and 550 pre-existing duplicate rows were cleaned
  retroactively, correcting the tier on 22 companies.
- **Silent-zero on rate limiting.** A failed Clay search (rate limit,
  network error) and a search that genuinely found nobody both used to
  produce the same result: zero qualified coordinators, and the company
  got disqualified. Confirmed live that concurrent search during a
  batch run triggered sustained rate limiting, silently zeroing out
  ~33% of one run's candidates. Fixed by treating "search failed" and
  "search succeeded with no results" as distinct outcomes -- only the
  second is a real disqualification.
- **Under-counting from a narrow title classifier.** 76 real people
  with legitimate industry-specific titles ("Deposition Coordinator,"
  "Calendar Coordinator") were being classified as ambiguous and never
  counted toward any company's tier. Found by direct inspection of
  people sitting in review status; fixed by widening the classifier's
  pattern list against real observed titles.
- **Roll-up subsidiaries slipping through.** A company's own
  description text (e.g. "Orange Legal, a Veritext Company") is real
  evidence it's a subsidiary of an already-known parent, not an
  independent target -- but the field carrying that text was defined in
  the schema and never actually populated by any source. Fixed by
  wiring the field through, plus a name-pattern fallback for companies
  already collected before the fix.

The common thread: every fix came from noticing a number was
implausibly low and tracing it to a real defect, never from assuming
the ICP was just strict.
