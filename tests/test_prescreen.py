from backend.qualification.prescreen import _stems, prescreen_batch, prescreen_company

COORDINATOR_TITLES = [
    "scheduler",
    "scheduling coordinator",
    "scheduling manager",
    "coordinator",
    "operations coordinator",
    "dispatch coordinator",
]


def test_stems_extracts_meaningful_words():
    stems = _stems(COORDINATOR_TITLES)
    assert "scheduling" in stems
    assert "coordinator" in stems
    assert "dispatch" in stems


def test_prescreen_company_no_website_is_none():
    assert prescreen_company(None, COORDINATOR_TITLES) is None


def test_prescreen_company_hit(monkeypatch):
    class _FakeResponse:
        text = "<html><body>Meet our Scheduling Team and Case Coordinators</body></html>"

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        return _FakeResponse()

    monkeypatch.setattr("backend.qualification.prescreen.httpx.get", fake_get)
    assert prescreen_company("https://example.com", COORDINATOR_TITLES) is True


def test_prescreen_company_miss(monkeypatch):
    class _FakeResponse:
        text = "<html><body>We provide court reporting services nationwide.</body></html>"

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        return _FakeResponse()

    monkeypatch.setattr("backend.qualification.prescreen.httpx.get", fake_get)
    assert prescreen_company("https://example.com", COORDINATOR_TITLES) is False


def test_prescreen_company_fetch_failure_is_none(monkeypatch):
    import httpx

    def fake_get(url, **kwargs):
        raise httpx.ConnectTimeout("boom")

    monkeypatch.setattr("backend.qualification.prescreen.httpx.get", fake_get)
    assert prescreen_company("https://example.com", COORDINATOR_TITLES) is None


def test_prescreen_batch_runs_concurrently_and_maps_results(monkeypatch):
    class _FakeResponse:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        if "hit" in url:
            return _FakeResponse("Our scheduling coordinators are the best")
        return _FakeResponse("Nothing relevant here")

    monkeypatch.setattr("backend.qualification.prescreen.httpx.get", fake_get)
    items = [(1, "https://hit.example.com"), (2, "https://miss.example.com"), (3, None)]
    results = prescreen_batch(items, COORDINATOR_TITLES)
    assert results == {1: True, 2: False, 3: None}
