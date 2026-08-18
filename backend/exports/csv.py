"""CSV exports: ready (Woodpecker import), research (master), qa_sample
(20-row human check), rejected (funnel/source-quality visibility).
"""

from __future__ import annotations

import csv
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from backend.campaigns.configs.base import CampaignConfig
from backend.models.database import (
    BuyingSignalRecord,
    Company,
    DecisionMakerStatusRecord,
    Person,
    Qualification,
)
from backend.models.schemas import RejectedCompany
from backend.research.evidence import get_research_map

logger = logging.getLogger(__name__)

READY_COLUMNS = [
    "FIRST_NAME", "LAST_NAME", "EMAIL", "COMPANY", "DOMAIN", "ROLE_TITLE",
    "TIER", "ROLE_COUNT", "VOLUME_VAR", "COVERAGE_VAR", "SIGNAL_PHRASE",
    "CITY", "STATE", "LINKEDIN_URL", "EMPLOYEE_COUNT", "INDUSTRY",
    "QUALIFICATION_REASON", "CAMPAIGN",
]

QA_COLUMNS = [
    "company", "domain", "tier", "coordinator_count", "employee_count",
    "volume_var", "coverage_var", "signal_phrase", "decision_maker",
    "decision_maker_title", "email", "research_source_urls", "research_evidence",
]

REJECTED_COLUMNS = ["company", "domain", "source", "rejection_stage", "rejection_reason"]


@dataclass
class PipelineRecord:
    company: Company
    qualification: Qualification
    decision_maker: Person | None
    decision_maker_status: DecisionMakerStatusRecord | None
    research_map: dict = field(default_factory=dict)
    signals: list = field(default_factory=list)


def _signal_phrase(signals: list[BuyingSignalRecord]) -> str | None:
    if not signals:
        return None
    ranked = sorted(signals, key=lambda s: {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "NONE": 3}.get(s.confidence, 3))
    best = ranked[0]
    return best.evidence_text or best.signal_value or best.signal_type


def gather_pipeline_records(session: Session, campaign: CampaignConfig) -> list[PipelineRecord]:
    """Every company that reached qualification (passed or not) -- the
    research CSV is meant to be the master file with full visibility into
    why a researched company was or wasn't qualified, not just the
    winners. build_ready_rows applies the strict Tier A/B + verified
    email + no-duplicates filter on top of this for the Woodpecker-ready
    export."""
    qualifications = session.query(Qualification).filter(Qualification.campaign_id == campaign.key).all()

    records: list[PipelineRecord] = []
    for q in qualifications:
        company = session.query(Company).get(q.company_id)
        if company is None:
            continue

        dm_status = (
            session.query(DecisionMakerStatusRecord)
            .filter(DecisionMakerStatusRecord.company_id == company.id, DecisionMakerStatusRecord.campaign_id == campaign.key)
            .one_or_none()
        )
        decision_maker = session.query(Person).get(dm_status.person_id) if dm_status else None

        research_map = get_research_map(session, company.id, campaign.key)
        signals = (
            session.query(BuyingSignalRecord)
            .filter(BuyingSignalRecord.company_id == company.id, BuyingSignalRecord.campaign_id == campaign.key)
            .all()
        )

        records.append(PipelineRecord(company, q, decision_maker, dm_status, research_map, signals))

    return records


def _is_export_eligible(rec: PipelineRecord) -> bool:
    return bool(
        rec.qualification.tier in ("A", "B")
        and rec.decision_maker is not None
        and rec.decision_maker_status is not None
        and rec.decision_maker_status.employment_verified
        and rec.decision_maker_status.email_status == "FOUND"
        and rec.decision_maker_status.email
    )


def personalization_gap_reason(rec: PipelineRecord, campaign: CampaignConfig) -> str | None:
    """Why an otherwise fully-qualified record (Tier A/B, verified
    decision maker, usable email) still won't appear in ready.csv.

    Only coverage_field (metro_count) is required -- confirmed live that
    requiring volume_field (reporter_count) too was blocking every real
    Tier A prospect found so far (Lexitas, Veritext), because most
    agencies simply don't publish a named reporter roster page; that's a
    real absence, not a research failure, and it shouldn't cost the
    prospect its export. volume_field is still surfaced as VOLUME_VAR
    when Claygent finds it, just no longer required. None means it's
    actually ready."""
    if not _is_export_eligible(rec):
        return None  # excluded for a different, already-logged reason
    coverage = rec.research_map.get(campaign.coverage_field)
    if coverage and coverage.value:
        return None
    return (
        f"Tier {rec.qualification.tier}, verified decision maker and email found, but "
        f"required personalization field came back NONE: {campaign.coverage_field}"
    )


def build_ready_rows(records: list[PipelineRecord], campaign: CampaignConfig) -> list[dict]:
    rows: list[dict] = []
    seen_emails: set[str] = set()
    seen_domains: set[str] = set()

    for rec in records:
        if not _is_export_eligible(rec):
            continue

        volume = rec.research_map.get(campaign.volume_field)
        coverage = rec.research_map.get(campaign.coverage_field)
        # Only coverage_field is mandatory -- per user direction, a real
        # reporter roster page frequently doesn't exist, and that
        # shouldn't hold back an otherwise fully-qualified prospect.
        # volume_field is still included in the row below when known.
        if not coverage or not coverage.value:
            continue

        email = rec.decision_maker_status.email
        domain = rec.company.canonical_domain
        if email.lower() in seen_emails or domain in seen_domains:
            continue  # no duplicate email, no duplicate company per campaign
        seen_emails.add(email.lower())
        seen_domains.add(domain)

        rows.append(
            {
                "FIRST_NAME": rec.decision_maker.first_name or "",
                "LAST_NAME": rec.decision_maker.last_name or "",
                "EMAIL": email,
                "COMPANY": rec.company.company_name,
                "DOMAIN": domain or "",
                "ROLE_TITLE": rec.decision_maker.raw_title or "",
                "TIER": rec.qualification.tier,
                "ROLE_COUNT": rec.qualification.coordinator_count,
                "VOLUME_VAR": volume.value if volume and volume.value else "",
                "COVERAGE_VAR": coverage.value,
                "SIGNAL_PHRASE": _signal_phrase(rec.signals) or "",
                "CITY": rec.company.city or "",
                "STATE": rec.company.state or "",
                "LINKEDIN_URL": rec.decision_maker.linkedin_url or "",
                "EMPLOYEE_COUNT": rec.company.employee_count or "",
                "INDUSTRY": rec.company.industry or "",
                "QUALIFICATION_REASON": rec.qualification.qualification_reason or "",
                "CAMPAIGN": campaign.key,
            }
        )
    return rows


def build_research_rows(records: list[PipelineRecord], campaign: CampaignConfig) -> list[dict]:
    # "job_openings" isn't a Claygent research field in the campaign
    # config (it's a Clay-managed function called during firmographic
    # filtering) but it's stored as a research_results row like the
    # others, so it belongs in the same evidence-backed column set.
    field_names = [f.name for f in campaign.sorted_research_fields()] + ["job_openings"]
    rows: list[dict] = []

    for rec in records:
        row = {
            "company": rec.company.company_name,
            "domain": rec.company.canonical_domain or "",
            "website": rec.company.website or "",
            "identity_confidence": rec.company.identity_confidence,
            "employees": rec.company.employee_count or "",
            "revenue": rec.company.revenue or "",
            "industry": rec.company.industry or "",
            "city": rec.company.city or "",
            "state": rec.company.state or "",
            "country": rec.company.country or "",
            "tier": rec.qualification.tier or "",
            "coordinator_count": rec.qualification.coordinator_count,
            "qualification_reason": rec.qualification.qualification_reason or "",
            "qualification_confidence": rec.qualification.qualification_confidence,
            "decision_maker": (
                f"{rec.decision_maker.first_name or ''} {rec.decision_maker.last_name or ''}".strip()
                if rec.decision_maker else ""
            ),
            "decision_maker_title": rec.decision_maker.raw_title if rec.decision_maker else "",
            "decision_maker_linkedin": rec.decision_maker.linkedin_url if rec.decision_maker else "",
            "employment_verified": rec.decision_maker_status.employment_verified if rec.decision_maker_status else "",
            "email": rec.decision_maker_status.email if rec.decision_maker_status else "",
            "email_source": rec.decision_maker_status.email_source if rec.decision_maker_status else "",
            "email_status": rec.decision_maker_status.email_status if rec.decision_maker_status else "",
            "buying_signals": "; ".join(s.signal_type for s in rec.signals),
            "buying_signal_evidence": "; ".join(filter(None, (s.evidence_text for s in rec.signals))),
            "personalization_volume_var": (rec.research_map.get(campaign.volume_field).value if rec.research_map.get(campaign.volume_field) else ""),
            "personalization_coverage_var": (rec.research_map.get(campaign.coverage_field).value if rec.research_map.get(campaign.coverage_field) else ""),
            "personalization_signal_phrase": _signal_phrase(rec.signals) or "",
        }

        for fname in field_names:
            r = rec.research_map.get(fname)
            row[f"{fname}_value"] = r.value if r else ""
            row[f"{fname}_confidence"] = r.confidence if r else ""
            row[f"{fname}_researched_at"] = r.researched_at.isoformat() if r and r.researched_at else ""
            evidence = r.evidence[0] if (r and r.evidence) else None
            row[f"{fname}_source_url"] = evidence.source_url if evidence else ""
            row[f"{fname}_evidence_text"] = evidence.evidence_text if evidence else ""

        rows.append(row)

    return rows


def write_csv(rows: list[dict], columns: list[str], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)


def write_ready_csv(records: list[PipelineRecord], campaign: CampaignConfig, path: Path) -> int:
    rows = build_ready_rows(records, campaign)
    return write_csv(rows, READY_COLUMNS, path)


def write_research_csv(records: list[PipelineRecord], campaign: CampaignConfig, path: Path) -> int:
    rows = build_research_rows(records, campaign)
    field_names = [f.name for f in campaign.sorted_research_fields()]
    columns = list(rows[0].keys()) if rows else (
        ["company", "domain", "website", "identity_confidence", "employees", "revenue",
         "industry", "city", "state", "country", "tier", "coordinator_count",
         "qualification_reason", "qualification_confidence", "decision_maker",
         "decision_maker_title", "decision_maker_linkedin", "employment_verified",
         "email", "email_source", "email_status", "buying_signals",
         "buying_signal_evidence", "personalization_volume_var",
         "personalization_coverage_var", "personalization_signal_phrase"]
        + [f"{fn}_{suffix}" for fn in field_names for suffix in ("value", "confidence", "researched_at", "source_url", "evidence_text")]
    )
    return write_csv(rows, columns, path)


def write_qa_sample_csv(records: list[PipelineRecord], campaign: CampaignConfig, path: Path, n: int = 20) -> int:
    ready_rows = build_ready_rows(records, campaign)
    sample = random.sample(ready_rows, min(n, len(ready_rows))) if ready_rows else []

    # map back to the underlying record for evidence detail
    by_domain = {r.company.canonical_domain: r for r in records}
    rows = []
    for row in sample:
        rec = by_domain.get(row["DOMAIN"])
        source_urls, evidence_texts = [], []
        if rec:
            for r in rec.research_map.values():
                if r.evidence:
                    for ev in r.evidence:
                        if ev.source_url:
                            source_urls.append(ev.source_url)
                        if ev.evidence_text:
                            evidence_texts.append(ev.evidence_text)
        rows.append(
            {
                "company": row["COMPANY"],
                "domain": row["DOMAIN"],
                "tier": row["TIER"],
                "coordinator_count": row["ROLE_COUNT"],
                "employee_count": row["EMPLOYEE_COUNT"],
                "volume_var": row["VOLUME_VAR"],
                "coverage_var": row["COVERAGE_VAR"],
                "signal_phrase": row["SIGNAL_PHRASE"],
                "decision_maker": f"{row['FIRST_NAME']} {row['LAST_NAME']}".strip(),
                "decision_maker_title": row["ROLE_TITLE"],
                "email": row["EMAIL"],
                "research_source_urls": " | ".join(dict.fromkeys(source_urls)),
                "research_evidence": " | ".join(dict.fromkeys(evidence_texts)),
            }
        )
    return write_csv(rows, QA_COLUMNS, path)


def write_rejected_csv(rejected: list[RejectedCompany], path: Path) -> int:
    rows = [
        {
            "company": r.company,
            "domain": r.domain or "",
            "source": r.source or "",
            "rejection_stage": r.rejection_stage,
            "rejection_reason": r.rejection_reason,
        }
        for r in rejected
    ]
    return write_csv(rows, REJECTED_COLUMNS, path)
