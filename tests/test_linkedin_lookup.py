from backend.providers.clay.routines import ClayRoutines


class _FakeAccount:
    """Only linkedin_lookup configured -- mirrors account 3, which has
    just this one function, not the full MANAGED_ROUTINES set."""

    name = "3"

    def env(self, suffix):
        return "function:t_linkedin" if suffix == "ROUTINE_LINKEDIN_LOOKUP" else None


class _FakeClient:
    def __init__(self, result: dict):
        self.result = result
        self.calls: list[dict] = []

    def run_routine_sync(self, routine_id, items, timeout=180.0):
        self.calls.append(items[0]["inputs"])
        return {"status": "complete", "data": [{"id": "row-1", "status": "complete", "result": self.result}]}


def test_sends_all_four_inputs_with_plausible_key_spellings():
    client = _FakeClient({"linkedin url": "https://www.linkedin.com/in/janedoe"})
    routines = ClayRoutines(client, account=_FakeAccount())

    outcome = routines.find_person_linkedin_url(
        full_name="Jane Doe", work_email="jane@acme.com", company_name="Acme Reporting", company_domain="acme.com",
    )

    assert outcome.ok
    assert outcome.value == "https://www.linkedin.com/in/janedoe"
    sent = client.calls[0]
    assert sent["full_name"] == "Jane Doe"
    assert sent["work_email"] == "jane@acme.com"
    assert sent["company_name"] == "Acme Reporting"
    assert sent["domain"] == "acme.com"
    assert sent["company_domain"] == "acme.com"


def test_not_found_is_a_real_none_not_an_error():
    client = _FakeClient({"linkedin url": "NONE"})
    routines = ClayRoutines(client, account=_FakeAccount())
    outcome = routines.find_person_linkedin_url("Jane Doe", "jane@acme.com", "Acme Reporting", "acme.com")
    assert outcome.ok
    assert outcome.value is None


def test_missing_routine_id_reports_unavailable_not_a_crash():
    class _NoRoutineAccount:
        name = "3"

        def env(self, suffix):
            return None

    routines = ClayRoutines(_FakeClient({}), account=_NoRoutineAccount())
    outcome = routines.find_person_linkedin_url("Jane Doe", None, None, "acme.com")
    assert not outcome.ok
    assert "not configured" in outcome.error
