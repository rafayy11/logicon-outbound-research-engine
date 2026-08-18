from backend.campaigns.configs.court_reporting import COURT_REPORTING
from backend.contacts.people import discover_decision_maker
from backend.models.database import Company, DecisionMakerStatusRecord
from backend.models.schemas import ClayPersonResult


class _FakeClaySearch:
    """Records how many times Clay was actually queried."""

    def __init__(self, people: list[ClayPersonResult]):
        self.people = people
        self.call_count = 0

    def find_decision_makers(self, domain, title_priority, max_results=10):
        self.call_count += 1
        return self.people


def _make_company(session) -> Company:
    company = Company(company_name="Acme Reporting", canonical_domain="acmereporting.com", status="QUALIFIED")
    session.add(company)
    session.flush()
    return company


def test_second_call_reuses_existing_record_without_requerying_clay(session):
    company = _make_company(session)
    fake_search = _FakeClaySearch(
        [ClayPersonResult(first_name="Ed", last_name="Kerpius", raw_title="President", current_company_domain="acmereporting.com")]
    )

    person1, reason1 = discover_decision_maker(session, company, COURT_REPORTING, fake_search)
    assert person1 is not None
    assert reason1 is None
    assert fake_search.call_count == 1

    # Simulate a re-run against the same company/campaign (e.g. a company
    # that was qualified in a prior run and got re-collected this run).
    person2, reason2 = discover_decision_maker(session, company, COURT_REPORTING, fake_search)
    assert person2 is not None
    assert person2.id == person1.id
    assert fake_search.call_count == 1  # no second Clay query

    records = (
        session.query(DecisionMakerStatusRecord)
        .filter(DecisionMakerStatusRecord.company_id == company.id, DecisionMakerStatusRecord.campaign_id == COURT_REPORTING.key)
        .all()
    )
    assert len(records) == 1  # the bug this guards against: a second row breaks one_or_none() in export
