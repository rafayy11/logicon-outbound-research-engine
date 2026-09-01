from backend.providers.clay.search import _parse_person, _strip_credential_suffix


def test_credential_suffix_stripped_from_split_name():
    """Real case confirmed live 2026-08-21: Clay's raw 'name' field was
    'Renee CRR' -- CRR is a court-reporting certification (Certified
    Realtime Reporter), not a last name -- and it got parsed as one,
    corrupting the decision-maker/email lookup that searches on the
    real name."""
    raw = {"name": "Renee CRR", "matched_experiences": [{"title": "Scheduling Coordinator"}]}
    person = _parse_person(raw, "example.com")
    assert person.first_name == "Renee"
    assert person.last_name is None


def test_credential_suffix_stripped_from_direct_last_name_field():
    raw = {"first_name": "Natalia", "last_name": "CLVS", "matched_experiences": []}
    person = _parse_person(raw, "example.com")
    assert person.first_name == "Natalia"
    assert person.last_name is None


def test_normal_names_unaffected():
    raw = {"name": "Jamie Kirk", "matched_experiences": []}
    person = _parse_person(raw, "example.com")
    assert person.first_name == "Jamie"
    assert person.last_name == "Kirk"


def test_strip_credential_suffix_helper():
    assert _strip_credential_suffix("Smith CRR") == "Smith"
    assert _strip_credential_suffix("CRR") is None
    assert _strip_credential_suffix("Smith") == "Smith"
    assert _strip_credential_suffix(None) is None
    assert _strip_credential_suffix("") == ""
