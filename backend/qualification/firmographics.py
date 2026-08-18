"""Firmographic enrichment + hard-stop filter. Runs AFTER dedup/
suppression and BEFORE any per-field Claygent research -- this is the
cheapest Clay call (one managed function) and it's what protects the
expensive research stage from being wasted on companies that were never
going to qualify.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from sqlalchemy.orm import Session

from backend.models.database import Company, ProviderError, ResearchEvidence
from backend.models.database import ResearchResult as ResearchResultRow
from backend.models.schemas import CompanyStatus, RejectedCompany
from backend.providers.clay.routines import ClayRoutines, RoutineOutcome
from backend.qualification.coordinator import classify_title
from backend.qualification.disqualifiers import check_hard_firmographic, check_text_based_disqualifiers

logger = logging.getLogger(__name__)

_INT_RE = re.compile(r"\d+")
JOB_OPENINGS_PROMPT_VERSION = "v1"


def _parse_employee_count(value: str | None) -> int | None:
    if not value:
        return None
    m = _INT_RE.search(value.replace(",", ""))
    return int(m.group(0)) if m else None


def _pull_firmographic_extras(company: Company, outcome) -> None:
    """The employee_count function's result is a full firmographic record
    (name, industry, revenue, ...) -- pull the extra fields from the same
    call instead of spending a second Clay call on each."""
    item = outcome.raw or {}
    result = item.get("result") if isinstance(item, dict) else None
    if not isinstance(result, dict):
        return
    lowered = {k.lower(): v for k, v in result.items()}

    industry = lowered.get("industry name") or lowered.get("industry")
    if industry and not company.industry:
        company.industry = str(industry)

    revenue = lowered.get("revenue total") or lowered.get("revenue band") or lowered.get("annual revenue")
    if revenue and not company.revenue:
        company.revenue = str(revenue)


def _store_job_openings_result(
    session: Session, company: Company, campaign_key: str, outcome: RoutineOutcome
) -> None:
    idempotency_key = f"company:{company.id}:job_openings:{JOB_OPENINGS_PROMPT_VERSION}"

    result_dict = {}
    if outcome.raw and isinstance(outcome.raw, dict):
        result_dict = outcome.raw.get("result") or {}
    departments = result_dict.get("breakdownByDepartment") or []

    relevant_titles: list[str] = []
    for dept in departments:
        for title in dept.get("jobs", []):
            label, _, _ = classify_title(title)
            if label.value == "QUALIFIED_COORDINATION":
                relevant_titles.append(title)

    row = ResearchResultRow(
        company_id=company.id,
        campaign_id=campaign_key,
        field_name="job_openings",
        value=outcome.value,
        normalized_value=outcome.value.strip() if outcome.value else None,
        confidence="HIGH" if outcome.ok and outcome.value else "NONE",
        researched_at=datetime.utcnow(),
        prompt_version=JOB_OPENINGS_PROMPT_VERSION,
        idempotency_key=idempotency_key,
        status="SUCCESS" if outcome.ok else "FAILED",
    )
    session.add(row)
    session.flush()

    if outcome.ok and (relevant_titles or outcome.value):
        evidence_text = (
            f"Coordination-relevant openings: {', '.join(relevant_titles)}"
            if relevant_titles
            else "No coordination-relevant openings among current job postings"
        )
        session.add(
            ResearchEvidence(
                research_result_id=row.id,
                source_type="JOB_POSTING",
                evidence_text=evidence_text,
            )
        )


def enrich_job_openings_for_company(
    session: Session, company: Company, campaign_key: str, clay_routines: ClayRoutines
) -> None:
    """Real job-board data (department + exact job titles), not an AI
    guess -- confirmed live to return structured results like
    {"summaryOfFindings": "...", "breakdownByDepartment": [{"department",
    "jobs": [...]}]}. Stores it as a proper research row (with the flattened
    job list as evidence) so it's visible in research.csv, and separately
    flags whether any listed job title looks like real operational
    coordination work, using the same classifier coordinator discovery
    uses -- this is a second, more reliable signal source than Claygent's
    web-search-based hiring_signal field, not a replacement for it.

    Deliberately NOT called from the firmographic filter stage: job
    openings has zero influence on coordinator qualification or tiering
    (that comes entirely from Clay's people search + rule-based title
    classification in qualification/coordinator.py) -- it's purely
    supplementary evidence for research.csv. Confirmed live: only ~11% of
    firmographically-passed companies ever reach Tier A/B (83 passed, 9
    researched in one real run), so calling this at the firmographic
    stage was spending a Clay credit on job postings for companies that
    were never going to be researched or contacted. Called instead
    per-company, only once a company is confirmed Tier A/B, same gating
    research already uses."""
    if not clay_routines.is_managed_function_available("job_openings"):
        return
    idempotency_key = f"company:{company.id}:job_openings:{JOB_OPENINGS_PROMPT_VERSION}"
    already = session.query(ResearchResultRow).filter(ResearchResultRow.idempotency_key == idempotency_key).one_or_none()
    if already is not None:
        return
    outcome = clay_routines.enrich_field("job_openings", company.canonical_domain)
    _store_job_openings_result(session, company, campaign_key, outcome)
    session.commit()


def filter_companies(
    session: Session,
    companies: list[Company],
    clay_routines: ClayRoutines,
    run_id: int | None = None,
) -> tuple[list[Company], list[RejectedCompany]]:
    """Three passes instead of one big per-company loop, so the
    employee_count Clay call becomes ONE batched call covering the whole
    input batch instead of one sequential call per company. Confirmed
    live: a 25-company batch's employee_count alone took 5m40s at one call
    per company -- these companies have no dependency on each other, only
    within-company step order matters (can't check employee_count-based
    disqualifiers before employee_count is known).

    job_openings enrichment is deliberately NOT here -- see
    enrich_job_openings_for_company, called only for Tier A/B companies."""
    passed: list[Company] = []
    rejected: list[RejectedCompany] = []
    rejected_ids: set[int] = set()

    def reject(company: Company, reason: str, status: str = CompanyStatus.DISQUALIFIED.value) -> None:
        company.status = status
        if status == CompanyStatus.DISQUALIFIED.value:
            company.disqualification_reason = reason
        rejected.append(
            RejectedCompany(
                company=company.company_name,
                domain=company.canonical_domain,
                source=None,
                rejection_stage="firmographic_filter",
                rejection_reason=reason,
            )
        )
        rejected_ids.add(company.id)

    # Pass 1: free checks, no Clay spend.
    for company in companies:
        hard_reason = check_hard_firmographic(company)
        if hard_reason:
            reject(company, hard_reason)
            continue
        if not company.canonical_domain:
            # No domain -> nothing to enrich against. Don't spend Clay,
            # don't fabricate a pass -- flag for manual review.
            reject(
                company,
                "No resolvable company domain -- cannot enrich via Clay",
                status=CompanyStatus.MANUAL_REVIEW.value,
            )

    # Pass 2: batch employee_count for survivors that don't already have it.
    survivors = [c for c in companies if c.id not in rejected_ids]
    needs_count = {str(c.id): c for c in survivors if c.employee_count is None}
    if needs_count:
        outcomes = clay_routines.enrich_field_batch(
            "employee_count", {item_id: c.canonical_domain for item_id, c in needs_count.items()}
        )
        for item_id, company in needs_count.items():
            outcome = outcomes.get(item_id) or RoutineOutcome(ok=False, error="no result returned for this item")
            if not outcome.ok:
                session.add(
                    ProviderError(
                        run_id=run_id,
                        company_id=company.id,
                        operation="enrich_field:employee_count",
                        provider="clay",
                        error=outcome.error or "unknown error",
                        occurred_at=datetime.utcnow(),
                    )
                )
                # Provider failure -- don't disqualify on missing data,
                # send to manual review instead of guessing.
                reject(
                    company,
                    f"employee_count enrichment failed: {outcome.error}",
                    status=CompanyStatus.MANUAL_REVIEW.value,
                )
                continue
            company.employee_count = _parse_employee_count(outcome.value)
            _pull_firmographic_extras(company, outcome)

    # Pass 3: hard + text disqualifiers now that employee_count is known.
    survivors = [c for c in companies if c.id not in rejected_ids]
    for company in survivors:
        hard_reason = check_hard_firmographic(company)
        if hard_reason:
            reject(company, hard_reason)
            continue
        text_reason = check_text_based_disqualifiers(company.description)
        if text_reason:
            reject(company, text_reason)
            continue
        company.status = CompanyStatus.FIRMOGRAPHICALLY_FILTERED.value
        passed.append(company)

    session.commit()
    logger.info(
        "Firmographic filter: %d passed, %d rejected (of %d)",
        len(passed), len(rejected), len(companies),
    )
    return passed, rejected
