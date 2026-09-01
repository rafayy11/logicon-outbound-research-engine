from types import SimpleNamespace

from backend.qualification.disqualifiers import (
    check_hard_firmographic,
    check_it_only_contact,
    check_rollup_subsidiary_name,
    check_text_based_disqualifiers,
)


def _company(**kwargs):
    defaults = dict(country="US", employee_count=50)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_fewer_than_11_employees_is_hard_disqualifier():
    reason = check_hard_firmographic(_company(employee_count=10))
    assert reason is not None
    assert "11" in reason


def test_eleven_employees_passes():
    assert check_hard_firmographic(_company(employee_count=11)) is None


def test_unknown_employee_count_does_not_disqualify():
    assert check_hard_firmographic(_company(employee_count=None)) is None


def test_non_us_is_hard_disqualifier():
    reason = check_hard_firmographic(_company(country="CA"))
    assert reason is not None


def test_text_based_pe_owned_detection():
    reason = check_text_based_disqualifiers("Acme Reporting is a portfolio company of Vista Capital.")
    assert reason is not None
    assert "PE-owned" in reason


def test_text_based_staff_aug_detection():
    reason = check_text_based_disqualifiers("We offer staff augmentation and dedicated developers.")
    assert reason is not None


def test_text_based_no_match_returns_none():
    assert check_text_based_disqualifiers("We provide court reporting services nationwide.") is None
    assert check_text_based_disqualifiers(None) is None


def test_it_only_contact_reachable():
    reason = check_it_only_contact(["IT Manager", "Systems Administrator"])
    assert reason is not None


def test_it_contact_with_operations_contact_is_fine():
    assert check_it_only_contact(["IT Manager", "Director of Operations"]) is None


def test_no_candidates_no_disqualification():
    assert check_it_only_contact([]) is None


def test_rollup_subsidiary_names_are_caught():
    # Real names confirmed live 2026-08-19 -- description was never
    # populated for any company, so this name-based check is currently
    # the only thing that can catch these.
    for name in [
        "Atkinson-Baker, a Veritext Company",
        "Discovery Resource, a Datavant Company",
        "Imber Court Reporters, a Lexitas Company",
        "Schmitt Reporting & Video, a Veritext Company",
    ]:
        reason = check_rollup_subsidiary_name(name)
        assert reason is not None, name


def test_rollup_check_does_not_flag_the_parent_itself():
    for name in ["Veritext, LLC", "Lexitas", "Datavant"]:
        assert check_rollup_subsidiary_name(name) is None, name


def test_rollup_check_does_not_flag_unrelated_names():
    for name in ["Barkley Court Reporters", "Capital Reporting Company", "ABC Court Reporting Company"]:
        assert check_rollup_subsidiary_name(name) is None, name


def test_rollup_check_empty_name():
    assert check_rollup_subsidiary_name("") is None
    assert check_rollup_subsidiary_name(None) is None
