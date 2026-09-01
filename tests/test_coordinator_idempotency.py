from backend.campaigns.configs.court_reporting import COURT_REPORTING
from backend.models.database import Company, CoordinatorClassification, Person
from backend.models.schemas import ClayPersonResult
from backend.qualification.coordinator import persist_coordinators, qualified_coordinator_count


def _make_company(session) -> Company:
    company = Company(company_name="Acme Reporting", canonical_domain="acmereporting.com", status="FIRMOGRAPHICALLY_FILTERED")
    session.add(company)
    session.flush()
    return company


def test_second_call_does_not_duplicate_the_same_person(session):
    """Guards against the real bug found live 2026-08-19: persist_coordinators
    had no idempotency check, so a company whose coordinator search ran
    more than once (overlapping runs) got the same real person inserted
    as a second Person + CoordinatorClassification row, inflating
    qualified_coordinator_count and, for boundary cases, the tier."""
    company = _make_company(session)
    people_results = [
        ClayPersonResult(first_name="Candice", last_name="Corum", raw_title="Scheduling Coordinator", current_company_domain="acmereporting.com"),
    ]

    persist_coordinators(session, company, COURT_REPORTING, people_results)
    persist_coordinators(session, company, COURT_REPORTING, people_results)  # simulates a re-run

    people = session.query(Person).filter(Person.company_id == company.id).all()
    assert len(people) == 1

    classifications = (
        session.query(CoordinatorClassification)
        .join(Person, Person.id == CoordinatorClassification.person_id)
        .filter(Person.company_id == company.id)
        .all()
    )
    assert len(classifications) == 1
    assert qualified_coordinator_count(session, company.id, COURT_REPORTING.key) == 1


def test_a_genuinely_different_person_still_gets_added(session):
    company = _make_company(session)
    persist_coordinators(
        session, company, COURT_REPORTING,
        [ClayPersonResult(first_name="Candice", last_name="Corum", raw_title="Scheduling Coordinator", current_company_domain="acmereporting.com")],
    )
    persist_coordinators(
        session, company, COURT_REPORTING,
        [ClayPersonResult(first_name="Jamie", last_name="Kirk", raw_title="Dispatch Coordinator", current_company_domain="acmereporting.com")],
    )

    people = session.query(Person).filter(Person.company_id == company.id).all()
    assert len(people) == 2
    assert qualified_coordinator_count(session, company.id, COURT_REPORTING.key) == 2
