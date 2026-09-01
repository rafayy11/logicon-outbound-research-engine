"""Normalization + deduplication: turns a stream of RawCompany records
(possibly from multiple sources, possibly re-seen across runs) into a
canonical set of Company rows. Domain is the primary identity; name+
location is the fallback, with identity_confidence recorded either way.

Runs BEFORE any Clay spend, so a company already known from a prior run
or a different source in this same run is never re-priced.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from backend.models.database import Company, CompanySource
from backend.models.schemas import CompanyStatus, IdentityConfidence, RawCompany
from backend.utils.normalize import identity_key as compute_identity_key
from backend.utils.normalize import normalize_domain

logger = logging.getLogger(__name__)


def _find_existing(session: Session, domain: str | None, ikey: str | None) -> Company | None:
    if domain:
        existing = session.query(Company).filter(Company.canonical_domain == domain).one_or_none()
        if existing:
            return existing
    if ikey:
        existing = session.query(Company).filter(Company.identity_key == ikey).one_or_none()
        if existing:
            return existing
    return None


def resolve_company(session: Session, raw: RawCompany) -> Company:
    domain = normalize_domain(raw.domain or raw.website)
    ikey = compute_identity_key(raw.company_name, raw.city, raw.state)

    company = _find_existing(session, domain, ikey)
    now = datetime.utcnow()

    if company is None:
        confidence = (
            IdentityConfidence.HIGH if domain
            else IdentityConfidence.MEDIUM if ikey
            else IdentityConfidence.LOW
        )
        company = Company(
            canonical_domain=domain,
            company_name=raw.company_name,
            website=raw.website,
            phone=raw.phone,
            address=raw.address,
            city=raw.city,
            state=raw.state,
            country=raw.country,
            description=raw.description,
            identity_confidence=confidence.value,
            identity_key=ikey,
            status=CompanyStatus.DISCOVERED.value,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(company)
        session.flush()  # assign company.id
    else:
        company.last_seen_at = now
        # Fill gaps only -- never overwrite a known-good field with blank data.
        if not company.canonical_domain and domain:
            company.canonical_domain = domain
            company.identity_confidence = IdentityConfidence.HIGH.value
        company.website = company.website or raw.website
        company.phone = company.phone or raw.phone
        company.address = company.address or raw.address
        company.city = company.city or raw.city
        company.state = company.state or raw.state
        company.description = company.description or raw.description

    session.add(
        CompanySource(
            company_id=company.id,
            source=raw.source,
            source_url=raw.source_url,
            source_identifier=raw.source_identifier,
            collected_at=now,
        )
    )
    return company


def import_raw_companies(session: Session, raw_companies: list[RawCompany]) -> list[Company]:
    """Normalize + dedupe within this batch AND against everything already
    in the DB (prior runs, other sources). Returns the distinct set of
    Company rows touched."""
    seen_ids: set[int] = set()
    companies: list[Company] = []

    for raw in raw_companies:
        company = resolve_company(session, raw)
        if company.id not in seen_ids:
            seen_ids.add(company.id)
            companies.append(company)
        if company.status == CompanyStatus.DISCOVERED.value:
            company.status = CompanyStatus.DEDUPLICATED.value

    session.commit()
    logger.info(
        "Imported %d raw companies -> %d canonical companies after dedup",
        len(raw_companies), len(companies),
    )
    return companies
