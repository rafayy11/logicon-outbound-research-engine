#!/usr/bin/env python3
"""Full-visibility exports, on top of the standard ready/research/qa/
rejected CSVs the pipeline itself writes:

  1. Per-tier ready CSVs -- ready_tier_a.csv / _b.csv / _c.csv, each the
     same real, decision-maker-verified, email-found rows as ready.csv,
     split out by tier so each can be reviewed (and sent) separately.
  2. A full database export -- ALL companies in the database, one row
     each, regardless of status (not just the ones that reached
     qualification, which is all research.csv covers). Every firmographic,
     source, qualification, decision-maker, and research field this
     pipeline has ever collected for that company.

Read-only; safe to run any time, including while a campaign is in
progress (SQLite tolerates concurrent readers).

Usage:
    python scripts/export_all.py --campaign court_reporting
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from backend.campaigns.configs import available_campaigns, get_config  # noqa: E402
from backend.exports.csv import gather_pipeline_records, is_ready, write_ready_csv  # noqa: E402
from backend.models.database import (  # noqa: E402
    Company,
    CompanySource,
    DecisionMakerStatusRecord,
    Person,
    Qualification,
    ResearchResult,
    get_session,
    init_db,
)
from backend.research.evidence import get_research_map  # noqa: E402

RESEARCH_FIELD_ORDER = [
    "reporter_count", "metro_count", "open_scheduler_roles", "hiring_signal",
    "client_portal", "software_mentioned", "advertised_turnaround", "job_openings",
]

FULL_EXPORT_COLUMNS = (
    [
        "company_name", "domain", "website", "city", "state", "country",
        "employee_count", "industry", "revenue", "company_linkedin_url",
        "status", "disqualification_reason", "sources",
        "tier", "coordinator_count", "qualification_reason",
        "decision_maker_first_name", "decision_maker_last_name", "decision_maker_title",
        "decision_maker_linkedin_url", "employment_verified", "email", "email_status", "email_source",
    ]
    + [f"{f}_value" for f in RESEARCH_FIELD_ORDER]
    + [f"{f}_confidence" for f in RESEARCH_FIELD_ORDER]
)


def _write_csv(rows: list[dict], columns: list[str], path: Path) -> int:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def export_per_tier_ready(session, campaign, export_dir: Path, today: str) -> dict[str, int]:
    all_records = gather_pipeline_records(session, campaign)
    ready_records = [r for r in all_records if is_ready(r, campaign)]

    counts = {}
    for tier in ("A", "B", "C"):
        subset = [r for r in ready_records if r.qualification.tier == tier]
        path = export_dir / f"{campaign.key}_{today}_ready_tier_{tier.lower()}.csv"
        counts[tier] = write_ready_csv(subset, campaign, path)
    return counts


def export_full_database(session, campaign, export_dir: Path, today: str) -> int:
    company_sources: dict[int, set[str]] = defaultdict(set)
    for cs in session.query(CompanySource).all():
        company_sources[cs.company_id].add(cs.source)

    qualifications: dict[int, Qualification] = {
        q.company_id: q
        for q in session.query(Qualification).filter(Qualification.campaign_id == campaign.key).all()
    }
    dm_statuses: dict[int, DecisionMakerStatusRecord] = {
        d.company_id: d
        for d in session.query(DecisionMakerStatusRecord).filter(DecisionMakerStatusRecord.campaign_id == campaign.key).all()
    }

    rows: list[dict] = []
    for company in session.query(Company).all():
        q = qualifications.get(company.id)
        dm_status = dm_statuses.get(company.id)
        decision_maker = session.get(Person, dm_status.person_id) if dm_status else None
        research_map = get_research_map(session, company.id, campaign.key)

        row = {
            "company_name": company.company_name,
            "domain": company.canonical_domain or "",
            "website": company.website or "",
            "city": company.city or "",
            "state": company.state or "",
            "country": company.country or "",
            "employee_count": company.employee_count if company.employee_count is not None else "",
            "industry": company.industry or "",
            "revenue": company.revenue or "",
            "company_linkedin_url": company.linkedin_url or "",
            "status": company.status or "",
            "disqualification_reason": company.disqualification_reason or "",
            "sources": "; ".join(sorted(company_sources.get(company.id, set()))),
            "tier": q.tier if q else "",
            "coordinator_count": q.coordinator_count if q else "",
            "qualification_reason": (q.qualification_reason or q.disqualification_reason or "") if q else "",
            "decision_maker_first_name": decision_maker.first_name if decision_maker else "",
            "decision_maker_last_name": decision_maker.last_name if decision_maker else "",
            "decision_maker_title": decision_maker.raw_title if decision_maker else "",
            "decision_maker_linkedin_url": decision_maker.linkedin_url if decision_maker else "",
            "employment_verified": dm_status.employment_verified if dm_status else "",
            "email": dm_status.email if dm_status else "",
            "email_status": dm_status.email_status if dm_status else "",
            "email_source": dm_status.email_source if dm_status else "",
        }
        for field_name in RESEARCH_FIELD_ORDER:
            r = research_map.get(field_name)
            row[f"{field_name}_value"] = r.value if r and r.value else ""
            row[f"{field_name}_confidence"] = r.confidence if r else ""
        rows.append(row)

    path = export_dir / f"{campaign.key}_{today}_full_database_export.csv"
    return _write_csv(rows, FULL_EXPORT_COLUMNS, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Full-visibility exports on top of the standard pipeline CSVs")
    parser.add_argument("--campaign", required=True, help=f"One of: {', '.join(available_campaigns())}")
    args = parser.parse_args()

    init_db()
    session = get_session()
    campaign = get_config(args.campaign)

    export_dir = Path("data/exports")
    today = datetime.utcnow().strftime("%Y-%m-%d")

    tier_counts = export_per_tier_ready(session, campaign, export_dir, today)
    total_companies = export_full_database(session, campaign, export_dir, today)

    print(f"ready_tier_a.csv: {tier_counts['A']} rows")
    print(f"ready_tier_b.csv: {tier_counts['B']} rows")
    print(f"ready_tier_c.csv: {tier_counts['C']} rows")
    print(f"full_database_export.csv: {total_companies} rows (every company, every status)")


if __name__ == "__main__":
    main()
