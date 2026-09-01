import backend.sources.court_reporting.clay_icp_search as clay_icp_search
from backend.sources.court_reporting.clay_icp_search import _parse_company, collect

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


class _FakeClient:
    """Simulates a real Clay search: the server-side cursor for a given
    search_id persists across separate ClayClient connections (each
    collect() call makes a new one, same as production), so the shared
    call-count state lives at the class level, not per-instance. Records
    how many times a NEW search was created vs. an existing one
    continued, so tests can prove a second collect() call reuses the
    search_id instead of starting over."""

    instances: list["_FakeClient"] = []
    _run_calls_by_search_id: dict[str, int] = {}
    _next_search_id = 1

    def __init__(self, api_key):
        self.create_calls = 0
        _FakeClient.instances.append(self)

    @property
    def run_calls_by_search_id(self):
        return _FakeClient._run_calls_by_search_id

    def create_query_search(self, query, source_type):
        self.create_calls += 1
        sid = f"search-{_FakeClient._next_search_id}"
        _FakeClient._next_search_id += 1
        return sid

    def run_query_search(self, search_id):
        n = _FakeClient._run_calls_by_search_id.get(search_id, 0)
        _FakeClient._run_calls_by_search_id[search_id] = n + 1
        if n == 0:
            result = dict(REAL_RESULT)
            result["domain"] = "pagea.com"
            result["clay_company_id"] = 1
            return {"data": [result], "has_more": True}
        # exhausted on the second call for any given search_id
        return {"data": [], "has_more": False}

    def close(self):
        pass


def test_second_call_reuses_search_id_instead_of_starting_over(tmp_path, monkeypatch):
    _FakeClient.instances = []
    _FakeClient._run_calls_by_search_id = {}
    _FakeClient._next_search_id = 1
    monkeypatch.setattr(clay_icp_search, "ClayClient", _FakeClient)
    monkeypatch.setattr(clay_icp_search, "STATE_PATH", tmp_path / "state.json")

    first = collect(api_key="fake", max_results=1)
    assert len(first) == 1
    assert _FakeClient.instances[0].create_calls == 1

    second = collect(api_key="fake", max_results=1)
    # second call's client never creates a new search -- it reuses the
    # persisted search_id from the state file.
    assert _FakeClient.instances[1].create_calls == 0
    assert len(second) == 0  # that search_id is exhausted (has_more=False)


def test_exhausted_query_short_circuits_with_zero_api_calls(tmp_path, monkeypatch):
    _FakeClient.instances = []
    _FakeClient._run_calls_by_search_id = {}
    _FakeClient._next_search_id = 1
    monkeypatch.setattr(clay_icp_search, "ClayClient", _FakeClient)
    monkeypatch.setattr(clay_icp_search, "STATE_PATH", tmp_path / "state.json")

    collect(api_key="fake", max_results=1)  # first call: 1 result, stops before probing exhaustion
    collect(api_key="fake", max_results=1)  # second call: reuses search_id, discovers exhaustion
    assert _FakeClient.instances[1].run_calls_by_search_id  # it did call run once to discover exhaustion

    result = collect(api_key="fake", max_results=1)  # third call: already marked exhausted
    assert result == []
    assert len(_FakeClient.instances) == 2  # no ClayClient even constructed for the third call
