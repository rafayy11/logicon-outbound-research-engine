from backend.providers.clay.client import ClayAPIError
from backend.providers.clay.routines import _MISSING_FIELD_RE, _run_sync_autocorrect


def test_single_missing_field_no_trailing_json_junk():
    msg = 'Clay API error 400: {"message":"Invalid inputs for item row-1: Domain: Missing required field: Domain"}'
    assert _MISSING_FIELD_RE.findall(msg) == ["Domain"]


def test_multiple_missing_fields():
    msg = (
        'Clay API error 400: {"message":"Invalid inputs for item row-1: '
        "Company Professional Url: Missing required field: Company Professional Url; "
        "Company Name: Missing required field: Company Name; "
        'Lookback (Months): Missing required field: Lookback (Months)"}'
    )
    assert _MISSING_FIELD_RE.findall(msg) == [
        "Company Professional Url",
        "Company Name",
        "Lookback (Months)",
    ]


class _FakeClient:
    """Mimics ClayClient.run_routine_sync: fails once with a Clay-shaped
    400 on the wrong key name, succeeds once the caller sends the right one."""

    def __init__(self, expected_field: str):
        self.expected_field = expected_field
        self.calls: list[dict] = []

    def run_routine_sync(self, routine_id, items, timeout=180.0):
        inputs = items[0]["inputs"]
        self.calls.append(inputs)
        if self.expected_field not in inputs:
            raise ClayAPIError(400, f'{{"message":"Invalid inputs for item row-1: {self.expected_field}: Missing required field: {self.expected_field}"}}')
        return {"status": "complete", "data": [{"id": "row-1", "status": "complete", "result": {"value": "ok"}}]}


def test_autocorrect_resolves_semantic_alias_not_just_substring():
    """'prompt' vs 'Research Question' share no substring -- this only
    works through the alias table, exactly the real Clay function we hit."""
    client = _FakeClient(expected_field="Research Question")
    result = _run_sync_autocorrect(client, "function:t_x", "row-1", {"domain": "clay.com", "prompt": "hi"})
    assert result["data"][0]["result"]["value"] == "ok"
    assert "Research Question" in client.calls[-1]
    assert client.calls[-1]["Research Question"] == "hi"


def test_autocorrect_leaves_genuinely_unresolvable_field_alone():
    client = _FakeClient(expected_field="Lookback (Months)")
    try:
        _run_sync_autocorrect(client, "function:t_x", "row-1", {"domain": "clay.com"})
        assert False, "expected ClayAPIError to propagate"
    except ClayAPIError:
        pass
