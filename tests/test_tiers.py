from backend.models.schemas import Tier
from backend.qualification.tiers import tier_for_count


def test_tier_a_at_six_or_more():
    assert tier_for_count(6) == Tier.A
    assert tier_for_count(10) == Tier.A


def test_tier_b_between_three_and_five():
    assert tier_for_count(3) == Tier.B
    assert tier_for_count(4) == Tier.B
    assert tier_for_count(5) == Tier.B


def test_tier_c_one_or_two():
    assert tier_for_count(1) == Tier.C
    assert tier_for_count(2) == Tier.C


def test_zero_coordinators_no_tier():
    assert tier_for_count(0) is None
