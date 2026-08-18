"""Regression tests for the "search failure silently counted as zero"
bug: confirmed live that concurrent coordinator search (5-10 workers)
triggers Clay rate limiting, and a provider failure must never be
indistinguishable from a genuine empty result -- that would silently
disqualify a company we never actually got an answer for.
"""

from backend.campaigns.configs.court_reporting import COURT_REPORTING
from backend.contacts.people import discover_decision_maker
from backend.models.database import Company
from backend.models.schemas import ClayPersonResult
from backend.providers.clay.client import ClayError
from backend.providers.clay.search import ClaySearch


class _FailingClient:
    def iter_query_search(self, query, source_type, max_results=25):
        raise ClayError("simulated rate limit")
        yield  # pragma: no cover -- makes this a generator, never reached


class _EmptyClient:
    def iter_query_search(self, query, source_type, max_results=25):
        return iter([])


def test_find_people_returns_none_on_provider_failure_not_empty_list():
    search = ClaySearch(_FailingClient())
    result = search.find_people_at_company("example.com", ["scheduler"])
    assert result is None


def test_find_people_returns_empty_list_on_genuine_empty_result():
    search = ClaySearch(_EmptyClient())
    result = search.find_people_at_company("example.com", ["scheduler"])
    assert result == []
    assert result is not None


class _FakeSearchReturningNone:
    def find_decision_makers(self, domain, title_priority, max_results=10):
        return None


def test_discover_decision_maker_does_not_fabricate_a_reason_on_provider_failure(session):
    company = Company(company_name="Acme Reporting", canonical_domain="acmereporting.com", status="QUALIFIED")
    session.add(company)
    session.flush()

    person, reason = discover_decision_maker(session, company, COURT_REPORTING, _FakeSearchReturningNone())

    assert person is None
    # Must NOT claim "no candidates found" -- the search never completed.
    assert reason is None
    assert company.status == "MANUAL_REVIEW"
