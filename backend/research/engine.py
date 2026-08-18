"""Clay research engine. Runs the campaign's research fields in priority
order, one small deterministic Claygent-style call per field (never one
vague "research this company" call), caches by idempotency key, and
never fabricates a value -- NONE from Clay is stored as NONE.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.campaigns.configs.base import CampaignConfig, ResearchFieldConfig
from backend.models.database import Company, ProviderError
from backend.models.database import ResearchEvidence
from backend.models.database import ResearchResult as ResearchResultRow
from backend.models.schemas import CompanyStatus
from backend.providers.clay.routines import ClayRoutines, RoutineOutcome
from backend.research.prompts import PROMPT_VERSION

logger = logging.getLogger(__name__)


def _idempotency_key(company_id: int, field_name: str, prompt_version: str) -> str:
    return f"company:{company_id}:{field_name}:{prompt_version}"


def _get_cached(session: Session, idempotency_key: str) -> ResearchResultRow | None:
    row = (
        session.query(ResearchResultRow)
        .filter(ResearchResultRow.idempotency_key == idempotency_key)
        .one_or_none()
    )
    if row is None:
        return None
    if row.expires_at and row.expires_at < datetime.utcnow():
        return None  # expired -- caller will refresh
    if row.status != "SUCCESS":
        return None  # a previous failure isn't a cache hit
    return row


def _store_research_result(
    session: Session,
    company: Company,
    campaign_id: str,
    field: ResearchFieldConfig,
    outcome: RoutineOutcome,
    run_id: int | None,
    research_run_id: str | None,
) -> ResearchResultRow:
    key = _idempotency_key(company.id, field.name, PROMPT_VERSION)

    if not outcome.ok:
        session.add(
            ProviderError(
                run_id=run_id,
                company_id=company.id,
                operation=f"research_field:{field.name}",
                provider="clay",
                error=outcome.error or "unknown error",
                occurred_at=datetime.utcnow(),
            )
        )
        confidence = "NONE"
        value = None
    else:
        value = outcome.value  # None means Clay returned NONE -- a real, valid outcome
        confidence = outcome.confidence

    existing = (
        session.query(ResearchResultRow)
        .filter(ResearchResultRow.idempotency_key == key)
        .one_or_none()
    )
    row = existing or ResearchResultRow(idempotency_key=key)
    row.company_id = company.id
    row.campaign_id = campaign_id
    row.field_name = field.name
    row.value = value
    row.normalized_value = value.strip() if value else None
    row.confidence = confidence
    row.researched_at = datetime.utcnow()
    row.expires_at = datetime.utcnow() + timedelta(days=field.expires_days)
    row.prompt_version = PROMPT_VERSION
    row.research_run_id = research_run_id
    row.status = "SUCCESS" if outcome.ok else "FAILED"

    if existing is None:
        session.add(row)
    session.flush()

    if outcome.ok and (outcome.source_url or outcome.evidence_text):
        session.add(
            ResearchEvidence(
                research_result_id=row.id,
                source_url=outcome.source_url,
                source_type=field.source_type.value,
                evidence_text=outcome.evidence_text,
            )
        )

    return row


def research_field(
    session: Session,
    company: Company,
    campaign_id: str,
    field: ResearchFieldConfig,
    clay_routines: ClayRoutines,
    run_id: int | None = None,
    research_run_id: str | None = None,
) -> ResearchResultRow:
    """Single-field entry point, kept for callers that only need one
    field. research_company below batches all of a company's fields into
    one Clay call instead of calling this per field."""
    key = _idempotency_key(company.id, field.name, PROMPT_VERSION)
    cached = _get_cached(session, key)
    if cached is not None:
        return cached
    outcome = clay_routines.research_field(field.name, company.canonical_domain, field.prompt)
    return _store_research_result(session, company, campaign_id, field, outcome, run_id, research_run_id)


def research_company(
    session: Session,
    company: Company,
    campaign: CampaignConfig,
    clay_routines: ClayRoutines,
    run_id: int | None = None,
    research_run_id: str | None = None,
) -> list[ResearchResultRow]:
    """All of a company's research fields share one domain and differ only
    by prompt -- fetch whichever aren't already cached in ONE batched Clay
    call (one item per field) instead of one sequential call per field.
    Confirmed live this was the dominant per-company cost: 7 sequential
    round trips for a single Tier A/B company."""
    results: list[ResearchResultRow] = []
    pending_fields: list[ResearchFieldConfig] = []

    for field in campaign.sorted_research_fields():
        key = _idempotency_key(company.id, field.name, PROMPT_VERSION)
        cached = _get_cached(session, key)
        if cached is not None:
            results.append(cached)
        else:
            pending_fields.append(field)

    if pending_fields:
        outcomes = clay_routines.research_fields_for_company(
            company.canonical_domain, [(f.name, f.prompt) for f in pending_fields]
        )
        for field in pending_fields:
            outcome = outcomes.get(field.name) or RoutineOutcome(
                ok=False, error="no result returned for this item"
            )
            row = _store_research_result(session, company, campaign.key, field, outcome, run_id, research_run_id)
            results.append(row)

    company.status = CompanyStatus.RESEARCHED.value
    session.commit()
    return results


def research_companies(
    session: Session,
    companies: list[Company],
    campaign: CampaignConfig,
    clay_routines: ClayRoutines,
    run_id: int | None = None,
    research_run_id: str | None = None,
) -> dict[int, list[ResearchResultRow]]:
    out: dict[int, list[ResearchResultRow]] = {}
    for company in companies:
        out[company.id] = research_company(session, company, campaign, clay_routines, run_id, research_run_id)
    return out
