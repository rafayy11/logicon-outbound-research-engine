from backend.utils.normalize import (
    domain_to_title_case_name,
    identity_key,
    is_free_email_domain,
    normalize_company_name,
    normalize_domain,
    normalize_phone,
    parse_us_address,
)


def test_normalize_domain_variants():
    assert normalize_domain("https://www.example.com/") == "example.com"
    assert normalize_domain("http://example.com") == "example.com"
    assert normalize_domain("www.example.com") == "example.com"
    assert normalize_domain("example.com") == "example.com"
    assert normalize_domain("https://Example.COM/path?q=1") == "example.com"
    assert normalize_domain("example.com:8080") == "example.com"


def test_normalize_domain_none_for_junk():
    assert normalize_domain(None) is None
    assert normalize_domain("") is None
    assert normalize_domain("not a domain") is None


def test_normalize_company_name_strips_suffixes():
    assert normalize_company_name("Acme Reporting, Inc.") == "acme reporting"
    assert normalize_company_name("Acme Reporting LLC") == "acme reporting"
    assert normalize_company_name("  Acme   Reporting  ") == "acme reporting"


def test_identity_key_combines_name_and_location():
    k1 = identity_key("Acme Reporting Inc", "Houston", "TX")
    k2 = identity_key("Acme Reporting, LLC", "Houston", "TX")
    assert k1 == k2  # same company, different legal suffix -> same key

    k3 = identity_key("Acme Reporting Inc", "Dallas", "TX")
    assert k1 != k3  # different city -> different key


def test_is_free_email_domain_exact_matches():
    for d in ["gmail.com", "webtv.net", "sbcglobal.net", "AOL.COM"]:
        assert is_free_email_domain(d), d


def test_is_free_email_domain_rr_com_regional_suffix():
    # Real data: TCRA returned "gt.rr.com" as a "company" before this fix.
    assert is_free_email_domain("gt.rr.com")
    assert is_free_email_domain("nyc.rr.com")


def test_is_free_email_domain_real_company_not_flagged():
    assert not is_free_email_domain("acmereporting.com")
    assert not is_free_email_domain(None)


def test_normalize_phone():
    assert normalize_phone("(630) 803-5828") == "6308035828"
    assert normalize_phone("+1 630-803-5828") == "6308035828"
    assert normalize_phone(None) is None


def test_parse_us_address():
    result = parse_us_address("13101 Northwest Freeway Suite 210, Houston, TX 77040")
    assert result["city"] == "Houston"
    assert result["state"] == "TX"
    assert result["zip"] == "77040"


def test_parse_us_address_unparseable_returns_none_fields():
    result = parse_us_address("not a real address format")
    assert result["city"] is None
    assert result["state"] is None


def test_domain_to_title_case_name():
    assert domain_to_title_case_name("lexitaslegal.com") == "Lexitaslegal"
    assert domain_to_title_case_name("acme-reporting.com") == "Acme Reporting"
    assert domain_to_title_case_name(None) is None
