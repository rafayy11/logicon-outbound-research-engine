from backend.sources.court_reporting.clay_icp_search import _parse_company

# Trimmed from a real Clay company-search result (captured 2026-08-18).
REAL_RESULT = {
    "clay_company_id": 12345678,
    "name": "Barkley Court Reporters",
    "size": "51-200",
    "type": "Privately Held",
    "domain": "barkley.com",
    "country": "United States",
    "industry": "Legal Services",
    "location": "Los Angeles, California",
    "description": "Barkley provides court reporting and litigation support services.",
    "linkedin_url": "https://www.linkedin.com/company/barkley-court-reporters",
    "annual_revenue": "25M-75M",
}


def test_parses_real_result():
    company = _parse_company(REAL_RESULT)
    assert company is not None
    assert company.company_name == "Barkley Court Reporters"
    assert company.domain == "barkley.com"
    assert company.city == "Los Angeles"
    assert company.source == "clay_icp_search"
    assert company.source_identifier == "12345678"


def test_missing_domain_is_dropped():
    result = dict(REAL_RESULT)
    result["domain"] = ""
    assert _parse_company(result) is None


def test_missing_name_is_dropped():
    result = dict(REAL_RESULT)
    result["name"] = ""
    assert _parse_company(result) is None
