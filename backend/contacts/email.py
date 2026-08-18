"""Work-email discovery via Clay's managed work_email function. No
separate verification subsystem in this MVP (no Reoon) -- Clay's own
result is used as-is. Never fabricated: no usable email -> MISSING, and
that person does not enter the final Woodpecker-ready CSV.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from backend.models.database import DecisionMakerStatusRecord, Person
from backend.models.schemas import EmailStatus
from backend.providers.clay.routines import ClayRoutines


def find_work_email(
    session: Session,
    person: Person,
    clay_routines: ClayRoutines,
    company_name: str | None = None,
    company_social_url: str | None = None,
) -> tuple[str | None, str]:
    status = (
        session.query(DecisionMakerStatusRecord)
        .filter(DecisionMakerStatusRecord.person_id == person.id)
        .one_or_none()
    )

    if status is not None and status.email_status == EmailStatus.FOUND.value and status.email:
        # Already have a definitive answer from a prior run -- calling
        # Clay again would just re-spend a work_email credit to re-learn
        # the same email. MISSING is intentionally NOT treated this way:
        # unlike FOUND, it doesn't yet distinguish "Clay looked and there
        # really is no email" from a transient provider failure, so it
        # stays retryable.
        return status.email, status.email_status

    if not person.current_company_domain:
        if status:
            status.email_status = EmailStatus.MISSING.value
            session.commit()
        return None, EmailStatus.MISSING.value

    outcome = clay_routines.find_work_email(
        person.first_name or "",
        person.last_name or "",
        person.current_company_domain,
        company_name,
        company_social_url,
    )

    email = outcome.value if outcome.ok and outcome.value else None
    email_status = EmailStatus.FOUND.value if email else EmailStatus.MISSING.value

    if status:
        status.email = email
        status.email_source = "clay_managed_function:work_email"
        status.email_status = email_status
        session.commit()

    return email, email_status
