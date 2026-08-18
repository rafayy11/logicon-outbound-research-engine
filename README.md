# Logicon Outbound Research Engine

Turns the Logicon Outbound Playbook into a repeatable pipeline: vertical
source directories -> raw companies -> normalize/dedupe -> Clay
enrichment/research -> Logicon ICP + coordinator qualification -> tiering
-> decision maker -> work email -> campaign-ready CSVs for manual
Woodpecker upload. Not an email sender, not a Clay replacement -- this
app owns the business rules, Clay owns research/enrichment.

Built first: **court_reporting**, end to end. `process_serving` and
`ia_ime` get added as new config files once court_reporting is proven on
real data -- the engine itself never changes per vertical.

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
    court_reporting/ncra.py, state_associations.py
    google_places.py     # optional, skipped cleanly with no GOOGLE_MAPS_API_KEY
  providers/clay/         # ALL Clay-specific code; everything else uses normalized models
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
scripts/validate_clay.py, validate_google_places.py
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

Covers normalization, dedup (within and across runs/sources), suppression,
disqualifiers, coordinator classification, and tiering -- all pure-logic,
no network/Clay required.
