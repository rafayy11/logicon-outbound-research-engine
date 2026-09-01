#!/usr/bin/env python3
"""Build the court-reporting master ready CSV.

Inputs:
  - the current pipeline ready CSV
  - Logicon_ICP_Company_List_1.xlsx / _2.xlsx contact sheets
  - cached research_results in data/outbound_engine.db
  - optional Clay generic research routine for missing reporter/metro counts

The output keeps Woodpecker's existing merge fields and adds explicit
REPORTER_COUNT / METRO_COUNT columns so the two personalization facts are
not hidden behind VOLUME_VAR / COVERAGE_VAR.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.campaigns.configs.court_reporting import COURT_REPORTING
from backend.models.database import Company, ResearchResult, get_session, init_db
from backend.providers.clay.client import ClayClient
from backend.providers.clay.routines import ClayRoutines, RoutineOutcome, _call_and_extract_batch
from backend.utils.normalize import normalize_company_name, normalize_domain

ROOT = Path(__file__).resolve().parent.parent
EXPORT_DIR = ROOT / "data" / "exports"
# Manually-curated ICP workbooks live outside the repo (real business data --
# see data/manual_imports/ for the gitignored, checked-in-shape version).
# Point ICP_WORKBOOK_DIR at wherever yours are; nothing breaks if it's unset
# since build_master() skips any workbook path that doesn't exist.
WORKBOOK_DIR = Path(os.environ.get("ICP_WORKBOOK_DIR", ROOT / "data" / "manual_imports"))

DEFAULT_WORKBOOKS = [
    WORKBOOK_DIR / "Logicon_ICP_Company_List_1.xlsx",
    WORKBOOK_DIR / "Logicon_ICP_Company_List_2.xlsx",
]

OUTPUT_COLUMNS = [
    "FIRST_NAME",
    "LAST_NAME",
    "EMAIL",
    "COMPANY",
    "DOMAIN",
    "ROLE_TITLE",
    "TIER",
    "ROLE_COUNT",
    "REPORTER_COUNT",
    "METRO_COUNT",
    "VOLUME_VAR",
    "COVERAGE_VAR",
    "SIGNAL_PHRASE",
    "CITY",
    "STATE",
    "LINKEDIN_URL",
    "COMPANY_LINKEDIN_URL",
    "EMPLOYEE_COUNT",
    "INDUSTRY",
    "QUALIFICATION_REASON",
    "CAMPAIGN",
    "COUNT_SOURCE",
    "SOURCE_FILES",
]

GAP_COLUMNS = OUTPUT_COLUMNS + ["GAP_REASON"]


@dataclass
class MasterRow:
    values: dict[str, str]
    source_files: set[str] = field(default_factory=set)
    count_sources: set[str] = field(default_factory=set)

    def merge(self, other: "MasterRow") -> None:
        for key, value in other.values.items():
            if value and not self.values.get(key):
                self.values[key] = value
        self.source_files.update(other.source_files)
        self.count_sources.update(other.count_sources)


def _today() -> str:
    return date.today().isoformat()


def _string(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _yes(value) -> bool:
    return _string(value).lower().startswith("yes")


def _usable_email(value) -> str:
    email = _string(value)
    if not email or "@" not in email:
        return ""
    upper = email.upper()
    if upper in {"NOT FOUND", "IN PROGRESS WHEN CREDITS RAN OUT"}:
        return ""
    return email


def _split_name(name: str) -> tuple[str, str]:
    parts = [p for p in _string(name).split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _extract_count(value) -> str:
    """Return the first defensible integer from a Clay/workbook value.

    NONE-style outputs stay blank. Strings like "6 (St. Petersburg, ...)"
    become "6" because the workbook explicitly records Clay's numeric
    answer plus human-readable evidence in one cell.
    """
    text = _string(value)
    if not text:
        return ""
    upper = text.upper()
    if upper.startswith("NONE") or "NO SUCH PAGE" in upper or "NOT FOUND" in upper:
        return ""
    match = re.search(r"\b\d+\b", text)
    return match.group(0) if match else ""


def _clean_phrase(value) -> str:
    text = _string(value)
    return "" if text.upper().startswith("NONE") else text


def _dedupe_key(row: MasterRow) -> str:
    values = row.values
    email = values.get("EMAIL", "").strip().lower()
    if email:
        return f"email:{email}"
    domain = values.get("DOMAIN", "").strip().lower()
    if domain:
        return f"domain:{domain}"
    company_key = normalize_company_name(values.get("COMPANY")) or values.get("COMPANY", "").lower()
    return f"company:{company_key}"


def _latest_ready_csv() -> Path:
    candidates = sorted(EXPORT_DIR.glob("court_reporting_20*_ready.csv"))
    if not candidates:
        raise FileNotFoundError(f"No dated court_reporting ready CSV found in {EXPORT_DIR}")
    return candidates[-1]


def _base_values() -> dict[str, str]:
    return {col: "" for col in OUTPUT_COLUMNS}


def _read_ready_csv(path: Path) -> list[MasterRow]:
    rows: list[MasterRow] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            values = _base_values()
            for col in values:
                if col in raw:
                    values[col] = _string(raw[col])
            values["REPORTER_COUNT"] = _extract_count(raw.get("REPORTER_COUNT") or raw.get("VOLUME_VAR"))
            values["METRO_COUNT"] = _extract_count(raw.get("METRO_COUNT") or raw.get("COVERAGE_VAR"))
            values["VOLUME_VAR"] = values["REPORTER_COUNT"]
            values["COVERAGE_VAR"] = values["METRO_COUNT"]
            values["SIGNAL_PHRASE"] = _clean_phrase(values.get("SIGNAL_PHRASE"))
            values["CAMPAIGN"] = values["CAMPAIGN"] or COURT_REPORTING.key
            row = MasterRow(values=values, source_files={path.name})
            if values["REPORTER_COUNT"] or values["METRO_COUNT"]:
                row.count_sources.add("ready_csv")
            rows.append(row)
    return rows


def _company_domain_map_from_workbook(path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    try:
        df = pd.read_excel(path, sheet_name="Court Reporting", skiprows=3)
    except Exception:
        return mapping
    for _, raw in df.iterrows():
        company = _string(raw.get("Company Name"))
        domain = normalize_domain(_string(raw.get("Website")))
        company_key = normalize_company_name(company)
        if company_key and domain:
            mapping[company_key] = domain
    return mapping


def _db_company_maps() -> tuple[dict[str, Company], dict[str, Company]]:
    init_db()
    session = get_session()
    by_domain: dict[str, Company] = {}
    by_name: dict[str, Company] = {}
    for company in session.query(Company).all():
        if company.canonical_domain:
            by_domain[company.canonical_domain.lower()] = company
        name_key = normalize_company_name(company.company_name)
        if name_key and name_key not in by_name:
            by_name[name_key] = company
    session.close()
    return by_domain, by_name


def _read_workbook_contacts(path: Path, db_by_name: dict[str, Company]) -> list[MasterRow]:
    workbook_domains = _company_domain_map_from_workbook(path)
    rows: list[MasterRow] = []
    for sheet_name in ("CR Pilot Results", "New CR Candidates"):
        try:
            df = pd.read_excel(path, sheet_name=sheet_name, skiprows=3)
        except Exception:
            continue
        for _, raw in df.iterrows():
            if not _yes(raw.get("Ready to Send?")):
                continue
            email = _usable_email(raw.get("Email"))
            if not email:
                continue

            company_name = _string(raw.get("Company"))
            name_key = normalize_company_name(company_name)
            company = db_by_name.get(name_key or "")
            domain = ""
            if company and company.canonical_domain:
                domain = company.canonical_domain
            if not domain and name_key:
                domain = workbook_domains.get(name_key, "")
            if not domain:
                domain = normalize_domain(email.split("@", 1)[1])

            first, last = _split_name(_string(raw.get("Contact Name")))
            reporter_count = _extract_count(raw.get("Reporter Count"))
            metro_count = _extract_count(raw.get("Metro Count"))

            values = _base_values()
            values.update(
                {
                    "FIRST_NAME": first,
                    "LAST_NAME": last,
                    "EMAIL": email,
                    "COMPANY": company_name,
                    "DOMAIN": domain or "",
                    "ROLE_TITLE": _string(raw.get("Title")),
                    "TIER": "",
                    "ROLE_COUNT": _extract_count(raw.get("Coordinator Signal*")),
                    "REPORTER_COUNT": reporter_count,
                    "METRO_COUNT": metro_count,
                    "VOLUME_VAR": reporter_count,
                    "COVERAGE_VAR": metro_count,
                    "CITY": company.city if company and company.city else "",
                    "STATE": company.state if company and company.state else "",
                    "COMPANY_LINKEDIN_URL": company.linkedin_url if company and company.linkedin_url else "",
                    "EMPLOYEE_COUNT": str(company.employee_count) if company and company.employee_count else "",
                    "INDUSTRY": company.industry if company and company.industry else "",
                    "QUALIFICATION_REASON": _string(raw.get("Flag / Spot-Check Needed") or raw.get("Flag / Why It's Not Ready")),
                    "CAMPAIGN": COURT_REPORTING.key,
                }
            )
            row = MasterRow(values=values, source_files={f"{path.name}:{sheet_name}"})
            if reporter_count or metro_count:
                row.count_sources.add("workbook")
            rows.append(row)
    return rows


def _merge_rows(rows: Iterable[MasterRow]) -> list[MasterRow]:
    merged: OrderedDict[str, MasterRow] = OrderedDict()
    for row in rows:
        key = _dedupe_key(row)
        if key in merged:
            merged[key].merge(row)
        else:
            merged[key] = row
    return list(merged.values())


def _hydrate_counts_from_db(rows: list[MasterRow]) -> int:
    init_db()
    session = get_session()
    filled = 0
    try:
        for row in rows:
            domain = row.values.get("DOMAIN", "").lower()
            company_name = normalize_company_name(row.values.get("COMPANY"))
            company = None
            if domain:
                company = session.query(Company).filter(Company.canonical_domain == domain).one_or_none()
            if company is None and company_name:
                companies = session.query(Company).all()
                company = next(
                    (c for c in companies if normalize_company_name(c.company_name) == company_name),
                    None,
                )
            if company is None:
                continue
            results = {
                r.field_name: r
                for r in session.query(ResearchResult).filter(
                    ResearchResult.company_id == company.id,
                    ResearchResult.campaign_id == COURT_REPORTING.key,
                    ResearchResult.status == "SUCCESS",
                )
            }
            for field_name, output_col in (
                ("reporter_count", "REPORTER_COUNT"),
                ("metro_count", "METRO_COUNT"),
            ):
                if row.values.get(output_col):
                    continue
                value = _extract_count(results.get(field_name).value if results.get(field_name) else "")
                if value:
                    row.values[output_col] = value
                    row.count_sources.add("database")
                    filled += 1
            row.values["VOLUME_VAR"] = row.values.get("REPORTER_COUNT", "")
            row.values["COVERAGE_VAR"] = row.values.get("METRO_COUNT", "")
    finally:
        session.close()
    return filled


def _research_prompt(field_name: str) -> str:
    for field_config in COURT_REPORTING.research_fields:
        if field_config.name == field_name:
            return field_config.prompt
    raise KeyError(field_name)


def _clay_outcome_value(outcome: RoutineOutcome) -> str:
    if not outcome.ok:
        return ""
    return _extract_count(outcome.value)


def _enrich_missing_counts_with_clay(
    rows: list[MasterRow],
    api_key: str,
    routine_id: str,
    limit: int | None = None,
) -> int:
    if not api_key:
        raise ValueError("Clay API key is required for enrichment")
    if not routine_id:
        raise ValueError("Clay generic research routine id is required for enrichment")

    os.environ["CLAY_ROUTINE_RESEARCH_GENERIC"] = routine_id
    filled = 0
    submitted = 0
    with ClayClient(api_key=api_key, timeout=120.0) as client:
        routines = ClayRoutines(client)
        if not routines.routine_ids.get("research_generic"):
            raise ValueError("Clay generic research routine id was not configured")

        items = []
        item_targets: dict[str, tuple[MasterRow, str]] = {}
        for row_index, row in enumerate(rows, 1):
            domain = row.values.get("DOMAIN", "")
            if not domain:
                continue
            missing = []
            if not row.values.get("REPORTER_COUNT"):
                missing.append(("reporter_count", "REPORTER_COUNT"))
            if not row.values.get("METRO_COUNT"):
                missing.append(("metro_count", "METRO_COUNT"))
            if not missing:
                continue
            for field_name, output_col in missing:
                if limit is not None and submitted >= limit:
                    break
                item_id = f"row{row_index}:{field_name}"
                items.append(
                    {
                        "id": item_id,
                        "inputs": {
                            "domain": domain,
                            "prompt": _research_prompt(field_name),
                        },
                    }
                )
                item_targets[item_id] = (row, output_col)
                submitted += 1
            if limit is not None and submitted >= limit:
                break

        if not items:
            return 0

        print(f"submitting_clay_field_calls: {len(items)}", flush=True)
        outcomes = _call_and_extract_batch(client, routines.routine_ids["research_generic"], items)
        for item_id, outcome in outcomes.items():
            row, output_col = item_targets[item_id]
            value = _clay_outcome_value(outcome)
            if value:
                row.values[output_col] = value
                row.count_sources.add("clay")
                filled += 1
            row.values["VOLUME_VAR"] = row.values.get("REPORTER_COUNT", "")
            row.values["COVERAGE_VAR"] = row.values.get("METRO_COUNT", "")
    return filled


def _gap_reason(row: MasterRow) -> str:
    # Only metro_count gates "ready to send" -- reporter_count was dropped
    # as a required export field per explicit instruction ("remove the
    # reporter count"); it's still carried on the row when available, just
    # not required to clear the gap.
    return "" if row.values.get("METRO_COUNT") else "metro_count"


def _write_csv(path: Path, rows: list[dict[str, str]], columns: list[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def build_master(args: argparse.Namespace) -> tuple[list[MasterRow], list[MasterRow], dict[str, int]]:
    ready_csv = Path(args.ready_csv) if args.ready_csv else _latest_ready_csv()
    workbook_paths = [Path(p) for p in (args.workbooks or DEFAULT_WORKBOOKS)]
    _, db_by_name = _db_company_maps()

    rows: list[MasterRow] = []
    rows.extend(_read_ready_csv(ready_csv))
    for workbook in workbook_paths:
        if workbook.exists():
            rows.extend(_read_workbook_contacts(workbook, db_by_name))

    rows = _merge_rows(rows)
    db_filled = _hydrate_counts_from_db(rows)

    clay_filled = 0
    if args.enrich:
        load_dotenv()
        api_key = args.api_key or os.environ.get("CLAY_API_KEY") or ""
        routine_id = args.routine_id or os.environ.get("CLAY_ROUTINE_RESEARCH_GENERIC") or ""
        clay_filled = _enrich_missing_counts_with_clay(rows, api_key, routine_id, args.limit)

    complete = [r for r in rows if not _gap_reason(r)]
    gaps = [r for r in rows if _gap_reason(r)]

    stats = {
        "input_rows_after_dedupe": len(rows),
        "complete_rows": len(complete),
        "gap_rows": len(gaps),
        "db_values_filled": db_filled,
        "clay_values_filled": clay_filled,
    }
    return complete, gaps, stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Build court_reporting master ready CSV with reporter_count and metro_count")
    parser.add_argument("--ready-csv", help="Ready CSV to use. Defaults to latest data/exports/court_reporting_20*_ready.csv")
    parser.add_argument("--workbooks", nargs="*", help="ICP workbook paths. Defaults to Logicon_ICP_Company_List_1.xlsx and _2.xlsx in Downloads")
    parser.add_argument("--output", default=str(EXPORT_DIR / f"court_reporting_master_ready_{_today()}.csv"))
    parser.add_argument("--gaps-output", default=str(EXPORT_DIR / f"court_reporting_master_ready_{_today()}_gaps.csv"))
    parser.add_argument("--enrich", action="store_true", help="Call Clay for missing reporter/metro counts")
    parser.add_argument("--api-key", help="Clay API key. Prefer CLAY_API_KEY env/.env to avoid shell history.")
    parser.add_argument("--routine-id", help="Clay generic research routine id. Prefer CLAY_ROUTINE_RESEARCH_GENERIC env/.env.")
    parser.add_argument("--limit", type=int, help="Maximum number of missing field calls to submit to Clay")
    parser.add_argument("--dry-run", action="store_true", help="Print counts without writing CSVs")
    args = parser.parse_args()

    complete, gaps, stats = build_master(args)

    print("Court reporting master build")
    for key, value in stats.items():
        print(f"{key}: {value}")

    if args.dry_run:
        return 0

    output_rows = []
    for row in complete:
        row.values["SOURCE_FILES"] = "; ".join(sorted(row.source_files))
        row.values["COUNT_SOURCE"] = "; ".join(sorted(row.count_sources))
        output_rows.append(row.values)

    gap_rows = []
    for row in gaps:
        row.values["SOURCE_FILES"] = "; ".join(sorted(row.source_files))
        row.values["COUNT_SOURCE"] = "; ".join(sorted(row.count_sources))
        gap_row = dict(row.values)
        gap_row["GAP_REASON"] = _gap_reason(row)
        gap_rows.append(gap_row)

    output_count = _write_csv(Path(args.output), output_rows, OUTPUT_COLUMNS)
    gaps_count = _write_csv(Path(args.gaps_output), gap_rows, GAP_COLUMNS)
    print(f"wrote {output_count}: {args.output}")
    print(f"wrote {gaps_count}: {args.gaps_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
