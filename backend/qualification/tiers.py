"""Tiering -- the master qualifier. 6+ qualified coordinators = Tier A,
3-5 = Tier B, 1-2 = Tier C (parked, never reaches decision-maker/email
stages). 0 doesn't even earn a tier -- it's a straight disqualification,
distinct from "parked" Tier C.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from backend.models.database import Qualification
from backend.models.schemas import CompanyStatus, Confidence, Tier

TIER_A_MIN = 6
TIER_B_MIN = 3
TIER_C_MIN = 1


def tier_for_count(coordinator_count: int) -> Tier | None:
    if coordinator_count >= TIER_A_MIN:
        return Tier.A
    if coordinator_count >= TIER_B_MIN:
        return Tier.B
    if coordinator_count >= TIER_C_MIN:
        return Tier.C
    return None


def qualify_and_tier(session: Session, company, campaign_key: str, coordinator_count: int) -> Qualification:
    tier = tier_for_count(coordinator_count)

    existing = (
        session.query(Qualification)
        .filter(Qualification.company_id == company.id, Qualification.campaign_id == campaign_key)
        .one_or_none()
    )
    q = existing or Qualification(company_id=company.id, campaign_id=campaign_key)

    if tier is None:
        q.passed = False
        q.disqualification_reason = "No qualified coordination roles found (coordinator_count=0)"
        q.tier = None
        q.qualification_confidence = Confidence.HIGH.value
        company.status = CompanyStatus.DISQUALIFIED.value
        company.disqualification_reason = q.disqualification_reason
    else:
        q.passed = True
        q.disqualification_reason = None
        q.tier = tier.value
        q.qualification_reason = f"{coordinator_count} qualified coordinator(s) -> Tier {tier.value}"
        q.qualification_confidence = Confidence.HIGH.value
        company.status = CompanyStatus.QUALIFIED.value

    q.coordinator_count = coordinator_count
    q.qualified_at = datetime.utcnow()

    if existing is None:
        session.add(q)
    session.commit()
    return q
