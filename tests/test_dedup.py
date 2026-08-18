from backend.models.database import Company, CompanySource
from backend.models.schemas import IdentityConfidence, RawCompany
from backend.utils.dedup import import_raw_companies


def test_same_domain_across_sources_dedupes_to_one_company(session):
    raw = [
        RawCompany(company_name="Acme Reporting", domain="acme.com", source="ncra_prolink"),
        RawCompany(company_name="Acme Reporting Inc", website="https://www.acme.com/", source="state_court_reporter_association"),
        RawCompany(company_name="ACME REPORTING", domain="acme.com", source="google_places"),
    ]
    companies = import_raw_companies(session, raw)
    assert len(companies) == 1
    assert companies[0].canonical_domain == "acme.com"
    assert companies[0].identity_confidence == IdentityConfidence.HIGH.value

    sources = session.query(CompanySource).filter(CompanySource.company_id == companies[0].id).all()
    assert len(sources) == 3
    assert {s.source for s in sources} == {"ncra_prolink", "state_court_reporter_association", "google_places"}


def test_no_domain_falls_back_to_name_location_identity(session):
    raw = [
        RawCompany(company_name="Bayou Reporting Services", city="Houston", state="TX", source="ncra_prolink"),
        RawCompany(company_name="Bayou Reporting Services LLC", city="Houston", state="TX", source="google_places"),
    ]
    companies = import_raw_companies(session, raw)
    assert len(companies) == 1
    assert companies[0].identity_confidence == IdentityConfidence.MEDIUM.value


def test_different_companies_stay_separate(session):
    raw = [
        RawCompany(company_name="Acme Reporting", domain="acme.com", source="ncra_prolink"),
        RawCompany(company_name="Beta Reporting", domain="beta.com", source="ncra_prolink"),
    ]
    companies = import_raw_companies(session, raw)
    assert len(companies) == 2


def test_dedup_persists_across_separate_import_calls(session):
    """Simulates deduping against companies already in the DB from a
    previous run, not just within one batch."""
    import_raw_companies(session, [RawCompany(company_name="Acme Reporting", domain="acme.com", source="ncra_prolink")])
    companies = import_raw_companies(session, [RawCompany(company_name="Acme Reporting", domain="acme.com", source="google_places")])

    assert len(companies) == 1
    total_companies = session.query(Company).count()
    assert total_companies == 1
