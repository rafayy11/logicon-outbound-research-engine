from backend.models.schemas import CoordinatorClassificationLabel
from backend.qualification.coordinator import classify_title


def test_strong_qualifying_titles():
    for title in [
        "Scheduling Coordinator",
        "Scheduler",
        "Operations Coordinator",
        "Dispatch Coordinator",
        "Dispatcher",
        "Claims Coordinator",
        "Network Coordinator",
    ]:
        label, reason, confidence = classify_title(title)
        assert label == CoordinatorClassificationLabel.QUALIFIED_COORDINATION, title


def test_schedul_and_dispatch_stem_variants_qualify():
    """Real titles Clay returned for an actual court reporting agency --
    same function, different noun suffix than 'scheduler'/'dispatcher'."""
    for title in [
        "Scheduling Specialist",
        "Senior Scheduling Coordinator",
        "Regional Scheduling Coordinator",
        "Dispatch Specialist",
        "Scheduling Manager",
    ]:
        label, reason, confidence = classify_title(title)
        assert label == CoordinatorClassificationLabel.QUALIFIED_COORDINATION, title


def test_disqualifying_titles_override_bare_coordinator_word():
    for title in [
        "Marketing Coordinator",
        "HR Coordinator",
        "Recruiting Coordinator",
        "Customer Success Coordinator",
        "Event Coordinator",
        "Project Coordinator",
    ]:
        label, reason, confidence = classify_title(title)
        assert label == CoordinatorClassificationLabel.NOT_QUALIFIED, title


def test_bare_coordinator_is_review_not_auto_qualified():
    label, reason, confidence = classify_title("Coordinator")
    assert label == CoordinatorClassificationLabel.REVIEW


def test_industry_specific_coordinator_titles_qualify():
    """Confirmed live 2026-08-19: 76 real people with titles like these
    were sitting in REVIEW and never actually reviewed, so never counted
    toward any company's tier -- these are genuine court-reporting/
    litigation-support operational coordination, same function as
    "scheduling coordinator" under a different noun."""
    for title in [
        "Case Coordinator",
        "Deposition Coordinator",
        "Calendar Coordinator",
        "Transcript Coordinator",
        "Production Coordinator",
        "Client Services Coordinator",
        "Office Coordinator",
    ]:
        label, reason, confidence = classify_title(title)
        assert label == CoordinatorClassificationLabel.QUALIFIED_COORDINATION, title


def test_generic_unrelated_coordinator_titles_still_go_to_review():
    # Deliberately NOT added as auto-qualifying -- genuinely ambiguous or
    # unrelated to operational/provider coordination, unlike the titles
    # in test_industry_specific_coordinator_titles_qualify above.
    for title in ["Billing Coordinator", "Research Coordinator", "Training Coordinator", "Administrative Coordinator"]:
        label, reason, confidence = classify_title(title)
        assert label == CoordinatorClassificationLabel.REVIEW, title


def test_unrelated_title_not_qualified():
    label, reason, confidence = classify_title("Software Engineer")
    assert label == CoordinatorClassificationLabel.NOT_QUALIFIED


def test_empty_title_not_qualified():
    label, reason, confidence = classify_title(None)
    assert label == CoordinatorClassificationLabel.NOT_QUALIFIED
    label, reason, confidence = classify_title("")
    assert label == CoordinatorClassificationLabel.NOT_QUALIFIED
