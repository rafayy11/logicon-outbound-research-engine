"""Current-employment verification. Compares the person's
current_company_domain (from Clay's search result) against the company's
canonical_domain -- a mismatch means Clay found someone who used to work
there, or a same-named different company, and the person is rejected
rather than sent to email discovery.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from backend.models.database import Company, DecisionMakerStatusRecord, Person
from backend.utils.normalize import normalize_domain


def verify_employment(session: Session, person: Person, company: Company) -> bool:
    person_domain = normalize_domain(person.current_company_domain)
    company_domain = normalize_domain(company.canonical_domain)

    verified = bool(person_domain and company_domain and person_domain == company_domain)

    status = (
        session.query(DecisionMakerStatusRecord)
        .filter(DecisionMakerStatusRecord.person_id == person.id)
        .one_or_none()
    )
    if status is None:
        return verified  # no decision-maker-status row -- caller isn't tracking this person for export

    status.employment_verified = verified
    status.employment_source = "clay_search.current_company_domain"
    status.employment_verified_at = datetime.utcnow()
    session.commit()
    return verified
