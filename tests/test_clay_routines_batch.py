"""Regression tests for _call_and_extract_batch: correlating multi-item
Clay batch results back to the right caller-side item (company/field) by
id, and retrying only the items whose failure looked transient -- not the
whole batch.
"""

from backend.providers.clay.routines import _call_and_extract_batch


class _BatchFakeClient:
    """responders: one function per successive run_routine_sync call,
    each given the submitted items and returning a results-shaped dict."""

    def __init__(self, responders):
        self.responders = list(responders)
        self.calls: list[list[str]] = []

    def run_routine_sync(self, routine_id, items, timeout=180.0):
        self.calls.append([it["id"] for it in items])
        return self.responders.pop(0)(items)


def test_batch_correlates_outcomes_by_id_not_response_order():
    def responder(items):
        return {
            "status": "complete",
            "data": [
                {"id": "b", "status": "complete", "result": {"value": "2"}},
                {"id": "a", "status": "complete", "result": {"value": "1"}},
            ],
        }

    client = _BatchFakeClient([responder])
    items = [{"id": "a", "inputs": {"domain": "a.com"}}, {"id": "b", "inputs": {"domain": "b.com"}}]
    outcomes = _call_and_extract_batch(client, "routine-batch-correlate", items)
    assert outcomes["a"].value == "1"
    assert outcomes["b"].value == "2"


def test_batch_retries_only_the_transient_failed_item():
    def first(items):
        return {
            "status": "complete",
            "data": [
                {"id": "a", "status": "complete", "result": {"value": "1"}},
                {"id": "b", "status": "failed", "error": "unexpected error, please try again"},
            ],
        }

    def second(items):
        assert [it["id"] for it in items] == ["b"]
        return {"status": "complete", "data": [{"id": "b", "status": "complete", "result": {"value": "2"}}]}

    client = _BatchFakeClient([first, second])
    items = [{"id": "a", "inputs": {}}, {"id": "b", "inputs": {}}]
    outcomes = _call_and_extract_batch(client, "routine-batch-retry", items, retry_delay=0)
    assert outcomes["a"].value == "1"
    assert outcomes["b"].value == "2"
    assert client.calls[1] == ["b"]  # only the failed item was resubmitted, not "a" too


def test_batch_never_retries_a_permanent_config_error():
    def responder(items):
        return {
            "status": "complete",
            "data": [{"id": "a", "status": "failed", "error": "Missing required field: Domain"}],
        }

    client = _BatchFakeClient([responder])
    outcomes = _call_and_extract_batch(client, "routine-batch-permanent", [{"id": "a", "inputs": {}}])
    assert outcomes["a"].ok is False
    assert len(client.calls) == 1  # no retry attempted
