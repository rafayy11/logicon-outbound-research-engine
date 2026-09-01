from backend.models.database import Company, Qualification
from backend.qualification.firmographics import filter_companies


class _FakeClayRoutines:
    """Records which domains it was actually asked to enrich, so tests
    can prove an already-qualified company never gets re-checked."""

    def __init__(self):
        self.enrich_field_batch_calls: list[dict] = []

    def enrich_field_batch(self, field_name, domains_by_item_id):
        self.enrich_field_batch_calls.append(dict(domains_by_item_id))
        return {}  # empty result -- every item "fails" if actually called


def _make_company(session, name, domain, employee_count=None) -> Company:
    company = Company(company_name=name, canonical_domain=domain, status="DEDUPLICATED", employee_count=employee_count)
    session.add(company)
    session.flush()
    return company


def test_already_qualified_company_skips_reverification(session):
    """Guards against the real bug found live 2026-08-20: three real
    Tier A/B/C companies (employee_count that had never resolved) got
    knocked from a valid tier to MANUAL_REVIEW on every re-run because
    the unrelated, unnecessary employee_count re-check kept failing --
    even though their actual qualification was already correct and
    untouched. A company with an existing Qualification must never be
    re-sent through this gate again."""
    qualified_co = _make_company(session, "Already Qualified Co", "already.com", employee_count=None)
    session.add(Qualification(company_id=qualified_co.id, campaign_id="court_reporting", tier="C", coordinator_count=1, passed=True))
    session.flush()

    new_co = _make_company(session, "Brand New Co", "brandnew.com", employee_count=None)

    fake_routines = _FakeClayRoutines()
    passed, rejected = filter_companies(session, [qualified_co, new_co], fake_routines, campaign_key="court_reporting")

    assert qualified_co in passed
    assert qualified_co.status == "DEDUPLICATED"  # untouched -- not re-set to MANUAL_REVIEW

    # the fake employee_count batch call was only ever asked about the
    # NEW company, never the already-qualified one
    all_requested_domains = {d for call in fake_routines.enrich_field_batch_calls for d in call.values()}
    assert "already.com" not in all_requested_domains
    assert "brandnew.com" in all_requested_domains
