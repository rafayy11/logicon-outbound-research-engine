"""Decision-maker discovery. Only called for Tier A/B companies -- Tier C
never reaches this stage (credit control). Picks the highest-priority
title match, not just the first person Clay returns.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from backend.campaigns.configs.base import CampaignConfig
from backend.models.database import DecisionMakerStatusRecord, Person
from backend.models.schemas import CompanyStatus, RoleCategory
from backend.providers.clay.search import ClaySearch
from backend.qualification.disqualifiers import check_it_only_contact

logger = logging.getLogger(__name__)


def _title_priority_rank(raw_title: str | None, campaign: CampaignConfig) -> int | None:
    if not raw_title:
        return None
    title_l = raw_title.lower()
    best_rank = None
    for dmt in campaign.decision_maker_titles:
        if dmt.title.lower() in title_l:
            if best_rank is None or dmt.rank < best_rank:
                best_rank = dmt.rank
    return best_rank


def discover_decision_maker(
    session: Session,
    company,
    campaign: CampaignConfig,
    clay_search: ClaySearch,
) -> tuple[Person | None, str | None]:
    """Returns (best decision-maker Person or None, disqualification_reason or None).

    Idempotent per (company, campaign): a re-run against a company that
    already has a decision-maker record (e.g. a prior run that qualified
    it but didn't finish exporting) reuses that record instead of
    re-querying Clay and inserting a second one -- both to avoid wasting
    a Clay search credit on an answer we already have, and because a
    second DecisionMakerStatusRecord row for the same company+campaign
    breaks every one_or_none() lookup downstream (confirmed live: this
    crashed CSV export on 2026-08-18)."""
    existing_status = (
        session.query(DecisionMakerStatusRecord)
        .filter(DecisionMakerStatusRecord.company_id == company.id, DecisionMakerStatusRecord.campaign_id == campaign.key)
        .one_or_none()
    )
    if existing_status is not None:
        existing_person = session.query(Person).get(existing_status.person_id)
        return existing_person, None

    titles = [dmt.title for dmt in campaign.decision_maker_titles]
    candidates = clay_search.find_decision_makers(company.canonical_domain, titles, max_results=10)

    company.status = CompanyStatus.DECISION_MAKER_PENDING.value

    if not candidates:
        session.commit()
        return None, "No decision-maker candidates found at this company"

    it_only_reason = check_it_only_contact([c.raw_title for c in candidates])
    if it_only_reason:
        company.status = CompanyStatus.DISQUALIFIED.value
        company.disqualification_reason = it_only_reason
        session.commit()
        return None, it_only_reason

    ranked = []
    for c in candidates:
        rank = _title_priority_rank(c.raw_title, campaign)
        if rank is not None:
            ranked.append((rank, c))
    ranked.sort(key=lambda pair: pair[0])

    if not ranked:
        session.commit()
        return None, "No candidate matched the campaign's decision-maker title priority list"

    best_rank, best = ranked[0]

    person = Person(
        company_id=company.id,
        first_name=best.first_name,
        last_name=best.last_name,
        raw_title=best.raw_title,
        normalized_title=(best.raw_title or "").strip().title() or None,
        linkedin_url=best.linkedin_url,
        current_company=best.current_company,
        current_company_domain=best.current_company_domain,
        role_category=RoleCategory.DECISION_MAKER.value,
        source="clay_search",
        discovered_at=datetime.utcnow(),
    )
    session.add(person)
    session.flush()

    session.add(
        DecisionMakerStatusRecord(
            person_id=person.id,
            company_id=company.id,
            campaign_id=campaign.key,
            title_priority_rank=best_rank,
            employment_verified=False,
            email_status="MISSING",
        )
    )

    company.status = CompanyStatus.DECISION_MAKER_FOUND.value
    session.commit()
    return person, None
