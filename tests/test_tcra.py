from backend.sources.court_reporting.tcra import TCRASource

# Trimmed from a real TCRA search-results page (www.tcra-online.com,
# captured 2026-08-18) -- structure preserved, most rows dropped.
SAMPLE_RESULTS_HTML = """
<html><body>
<table>
<tr valign="top">
<td width="82"></td>
<td class="tsAppBodyText tsAppBT20" width="100%">
<div class="tsAppMemberDirectoryUnlinkedName"><b>Smith, Jane</b></div>
<br/>
<div itemprop="address" itemscope itemtype="http://schema.org/PostalAddress">
<span itemprop="addressLocality">Austin</span>
Travis County <br />
</div>
<br/>
Address Work Phone: 5125551234<br/>
Email: <a href="mailto:jane.smith@acmereporting.com">jane.smith@acmereporting.com</a><br/>
Type of Reporter:
Freelance<br/>
</td>
</tr>
<tr valign="top">
<td width="82"></td>
<td class="tsAppBodyText tsAppBT20" width="100%">
<div class="tsAppMemberDirectoryUnlinkedName"><b>Doe, John</b></div>
<br/>
<div itemprop="address" itemscope itemtype="http://schema.org/PostalAddress">
<span itemprop="addressLocality">Dallas</span>
</div>
<br/>
Email: <a href="mailto:jdoe@gmail.com">jdoe@gmail.com</a><br/>
Type of Reporter:
Freelance<br/>
</td>
</tr>
<tr valign="top">
<td width="82"></td>
<td class="tsAppBodyText tsAppBT20" width="100%">
<div class="tsAppMemberDirectoryUnlinkedName"><b>NoEmail, Person</b></div>
<br/>
<div itemprop="address" itemscope itemtype="http://schema.org/PostalAddress">
<span itemprop="addressLocality">Houston</span>
</div>
<br/>
Type of Reporter:
Student<br/>
</td>
</tr>
</table>
<a href="/?&diraction=SearchResults&fs_match=c&ma_170_stateprov=TX&pg=members&seed=209615&memPageNum=2">Next Page &gt;&gt;</a>
</body></html>
"""


def test_parses_real_result_with_company_email():
    src = TCRASource()
    companies, next_url = src._parse_results(SAMPLE_RESULTS_HTML, "TX")
    domains = {c.domain for c in companies}
    assert "acmereporting.com" in domains
    acme = next(c for c in companies if c.domain == "acmereporting.com")
    assert acme.city == "Austin"
    assert acme.state == "TX"
    assert acme.phone == "5125551234"
    assert acme.source_identifier == "jane.smith@acmereporting.com"


def test_drops_free_email_provider():
    src = TCRASource()
    companies, _ = src._parse_results(SAMPLE_RESULTS_HTML, "TX")
    domains = {c.domain for c in companies}
    assert "gmail.com" not in domains


def test_drops_entry_with_no_email():
    src = TCRASource()
    companies, _ = src._parse_results(SAMPLE_RESULTS_HTML, "TX")
    assert len(companies) == 1  # only the acmereporting.com row survives


def test_extracts_next_page_url():
    src = TCRASource()
    _, next_url = src._parse_results(SAMPLE_RESULTS_HTML, "TX")
    assert next_url is not None
    assert "memPageNum=2" in next_url
    assert next_url.startswith("https://www.tcra-online.com")


def test_no_next_page_when_absent():
    src = TCRASource()
    html_no_next = SAMPLE_RESULTS_HTML.split('<a href="/?&diraction')[0]
    _, next_url = src._parse_results(html_no_next, "TX")
    assert next_url is None
