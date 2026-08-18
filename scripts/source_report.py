#!/usr/bin/env python3
"""Per-source funnel breakdown -- which source (NCRA, TCRA, Clay ICP
search, Google Places) actually produces qualified/exportable companies,
not just raw volume. Read-only; safe to run while a campaign is in
progress (SQLite tolerates concurrent readers).

Usage:
    python scripts/source_report.py --campaign court_reporting
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from backend.models.database import (  # noqa: E402
    Company,
    CompanySource,
    DecisionMakerStatusRecord,
    Qualification,
    get_session,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", required=True)
    args = parser.parse_args()

    session = get_session()

    # company_id -> set of sources that found it (a company can come from
    # more than one source; it's counted under each for this report).
    company_sources: dict[int, set[str]] = defaultdict(set)
    for cs in session.query(CompanySource).all():
        company_sources[cs.company_id].add(cs.source)

    companies_by_id = {c.id: c for c in session.query(Company).all()}
    qualifications = {
        q.company_id: q
        for q in session.query(Qualification).filter(Qualification.campaign_id == args.campaign).all()
    }
    dm_status_by_company = {
        d.company_id: d
        for d in session.query(DecisionMakerStatusRecord).filter(DecisionMakerStatusRecord.campaign_id == args.campaign).all()
    }

    stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for company_id, sources in company_sources.items():
        company = companies_by_id.get(company_id)
        if company is None:
            continue
        q = qualifications.get(company_id)
        dm = dm_status_by_company.get(company_id)

        for source in sources:
            stats[source]["raw"] += 1
            if company.employee_count is not None:
                stats[source]["firmographic_enriched"] += 1
            if company.status == "DISQUALIFIED":
                stats[source]["disqualified"] += 1
            if q is not None:
                stats[source]["researched_and_coordinator_checked"] += 1
                if q.tier == "A":
                    stats[source]["tier_a"] += 1
                elif q.tier == "B":
                    stats[source]["tier_b"] += 1
                elif q.tier == "C":
                    stats[source]["tier_c"] += 1
            if dm is not None:
                stats[source]["decision_maker_found"] += 1
                if dm.employment_verified:
                    stats[source]["employment_verified"] += 1
                if dm.email_status == "FOUND":
                    stats[source]["usable_email"] += 1

    columns = [
        ("raw", "raw"),
        ("firm_ok", "firmographic_enriched"),
        ("disq", "disqualified"),
        ("researched", "researched_and_coordinator_checked"),
        ("tier_a", "tier_a"),
        ("tier_b", "tier_b"),
        ("tier_c", "tier_c"),
        ("dm_found", "decision_maker_found"),
        ("emp_ver", "employment_verified"),
        ("email_ok", "usable_email"),
    ]
    col_width = 11

    header = f"{'source':<28}" + "".join(f"{label:>{col_width}}" for label, _ in columns)
    print(header)
    print("-" * len(header))
    for source in sorted(stats.keys()):
        row = stats[source]
        print(f"{source:<28}" + "".join(f"{row.get(key, 0):>{col_width}}" for _, key in columns))

    print(
        "\nNote: 'raw' counts a company once per source that found it -- a "
        "company found by two sources is counted under both, so column "
        "totals can exceed the campaign's total unique company count."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
