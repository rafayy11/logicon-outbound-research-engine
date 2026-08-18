"""Regression tests for ClayClient.run_routine_sync's batch-completeness
check. Before this fix, the only completeness check was `"data" in data`,
which was fine for a single-item call but wrong for a multi-item batch:
Clay's results endpoint can return a "data" array containing only the
items that finished SO FAR while others are still processing, so a
partially-finished batch would look "done" and silently drop the
still-pending items.

Builds a ClayClient without going through __init__ (no real api key / HTTP
client needed) and monkeypatches run_routine/get_routine_results directly
-- no network access required.
"""

from backend.providers.clay.client import ClayClient


def _make_client_stub(responses: list[dict]) -> ClayClient:
    client = ClayClient.__new__(ClayClient)
    remaining = list(responses)
    client.run_routine = lambda routine_id, items: "run-1"
    client.get_routine_results = lambda routine_run_id: remaining.pop(0)
    return client


def test_partial_batch_data_is_not_treated_as_complete():
    client = _make_client_stub(
        [
            {
                "status": "running",
                "total": 3,
                "finished": 1,
                "data": [{"id": "a", "status": "complete", "result": {"value": "1"}}],
            },
            {
                "status": "complete",
                "total": 3,
                "finished": 3,
                "data": [
                    {"id": "a", "status": "complete", "result": {"value": "1"}},
                    {"id": "b", "status": "complete", "result": {"value": "2"}},
                    {"id": "c", "status": "complete", "result": {"value": "3"}},
                ],
            },
        ]
    )
    result = client.run_routine_sync(
        "routine-1",
        [{"id": "a", "inputs": {}}, {"id": "b", "inputs": {}}, {"id": "c", "inputs": {}}],
        poll_interval=0,
    )
    assert {e["id"] for e in result["data"]} == {"a", "b", "c"}


def test_single_item_still_returns_as_soon_as_its_id_is_present():
    client = _make_client_stub(
        [{"status": "complete", "data": [{"id": "row-1", "status": "complete", "result": {"value": "ok"}}]}]
    )
    result = client.run_routine_sync("routine-1", [{"id": "row-1", "inputs": {}}], poll_interval=0)
    assert result["data"][0]["id"] == "row-1"
